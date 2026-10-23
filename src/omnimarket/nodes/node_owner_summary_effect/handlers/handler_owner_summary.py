# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Facts, shape check and send watermark for the twice-daily owner summary.

Three commands, none of which reads GitHub:

* ``facts``     what the factory did in a window. Merges come from the PR watcher's local
                state, with the canonical clones' first-parent history as a cross-check and
                gap fill. Lanes come from the work ledger. A source that cannot be read is an
                ``errors`` entry, never a guess.
* ``check``     validates a draft against the facts: footer, length, no token-shaped string, no
                home path, and every ticket id and ``repo#number`` the draft cites must be in
                the facts. A cited id the facts do not hold is a fabricated one.
* ``mark-sent`` records the window end as sent, so the next window starts where this one ended.

Authors are dropped from the facts on purpose: the summary refers to people by role, and a name
that is not in the facts cannot reach the draft.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from omnimarket.nodes.node_owner_summary_effect.models.model_clone_commit import (
    ModelCloneCommit,
)
from omnimarket.nodes.node_owner_summary_effect.models.model_lane_closed import (
    ModelLaneClosed,
)
from omnimarket.nodes.node_owner_summary_effect.models.model_lane_started import (
    ModelLaneStarted,
)
from omnimarket.nodes.node_owner_summary_effect.models.model_m4_row import ModelM4Row
from omnimarket.nodes.node_owner_summary_effect.models.model_merged_change import (
    ModelMergedChange,
)
from omnimarket.nodes.node_owner_summary_effect.models.model_owner_summary_facts import (
    ModelOwnerSummaryFacts,
)
from omnimarket.nodes.node_owner_summary_effect.models.model_owner_summary_request import (
    ModelOwnerSummaryRequest,
)
from omnimarket.nodes.node_owner_summary_effect.models.model_owner_summary_result import (
    ModelOwnerSummaryResult,
)

DEFAULT_WINDOW_HOURS = 12
MAX_WINDOW_HOURS = 48
MAX_WORDS = 420
TEXT_LIMIT = 240
LANE_LIST_LIMIT = 80
STALE_WATCHER_S = 1800
CONTROLLER_LANE = re.compile(r"^landing-L\d+")
M4_PATTERN = re.compile(r"\bM4\b|\bC(?:1[1-7]|2[89]|3[01])\b")
TICKET_PATTERN = re.compile(r"\bOMN-\d+\b")
PR_PATTERN = re.compile(r"\(#(\d+)\)")
REPO_PR_PATTERN = re.compile(r"\b([A-Za-z][A-Za-z0-9._-]*)#(\d+)\b")
FOOTER_PATTERN = re.compile(r"^\(via [A-Z][A-Za-z'-]*'s agent\)$")
SECRET_PATTERN = re.compile(
    r"xox[abprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}"
)
HOME_PATH_PATTERN = re.compile(r"/(?:Users|home|Volumes)/[A-Za-z]")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _tickets(text: str) -> tuple[str, ...]:
    return tuple(
        sorted(set(TICKET_PATTERN.findall(text)), key=lambda t: int(t.split("-")[1]))
    )


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if done.returncode:
        message = done.stderr.strip().splitlines()
        raise OSError(message[0] if message else "git failed")
    return done.stdout


class HandlerOwnerSummary:
    """Run one owner-summary command."""

    def handle(self, request: ModelOwnerSummaryRequest) -> ModelOwnerSummaryResult:
        if request.command == "facts":
            return self._facts(request)
        if request.command == "check":
            return self._check(request)
        return self._mark_sent(request)

    # -- window and watermark -------------------------------------------------

    @staticmethod
    def _state_file(request: ModelOwnerSummaryRequest) -> Path:
        return request.state_file or (
            request.root / ".onex_state" / "owner-summary" / "state.json"
        )

    def resolve_window(
        self, now: datetime, since: str, until: str, state_file: Path
    ) -> tuple[datetime, datetime, str]:
        """Return (since, until, source); the start is the last sent end when it is recent."""
        end = _parse(until) if until else now
        if end is None:
            raise ValueError(f"--until is not an ISO instant: {until}")
        if since:
            start = _parse(since)
            if start is None:
                raise ValueError(f"--since is not an ISO instant: {since}")
            return start, end, "--since"
        floor = end - timedelta(hours=MAX_WINDOW_HOURS)
        last: datetime | None = None
        try:
            last = _parse(
                str(
                    json.loads(state_file.read_text(encoding="utf-8")).get(
                        "last_sent_until", ""
                    )
                )
            )
        except (OSError, ValueError, AttributeError):
            last = None
        if last is not None and last < end:
            if last < floor:
                return floor, end, f"last-sent capped at {MAX_WINDOW_HOURS}h"
            return last, end, "last-sent"
        return (
            end - timedelta(hours=DEFAULT_WINDOW_HOURS),
            end,
            f"default {DEFAULT_WINDOW_HOURS}h",
        )

    def _mark_sent(self, request: ModelOwnerSummaryRequest) -> ModelOwnerSummaryResult:
        end = _parse(request.until)
        if end is None:
            return ModelOwnerSummaryResult(
                exit_code=2, message=f"--until is not an ISO instant: {request.until}"
            )
        path = self._state_file(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "last_sent_until": _iso(end),
                    "marked_at": _iso(datetime.now(UTC)),
                }
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
        return ModelOwnerSummaryResult(
            exit_code=0, message=f"marked sent through {_iso(end)}"
        )

    # -- sources ---------------------------------------------------------------

    @staticmethod
    def _watcher(
        path: Path, start: datetime, end: datetime, now: datetime, errors: list[str]
    ) -> tuple[list[ModelMergedChange], dict[str, Any], int | None]:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"pr watcher state unreadable: {path.name}: {exc}")
            return [], {}, None
        age: int | None = None
        last_tick = _parse(str(state.get("last_tick", "")))
        if last_tick is None:
            errors.append("pr watcher state has no last_tick")
        else:
            age = int((now - last_tick).total_seconds())
            if age > STALE_WATCHER_S:
                errors.append(f"pr watcher state is {age} s old; counts may be short")
        merged: list[ModelMergedChange] = []
        for record in (state.get("merges") or {}).values():
            merged_at = _parse(str(record.get("merged_at", "")))
            if merged_at is None or not (start < merged_at <= end):
                continue
            title = str(record.get("title", ""))
            merged.append(
                ModelMergedChange(
                    repo=str(record.get("repo", "")),
                    number=int(record.get("number", 0)),
                    title=title[:TEXT_LIMIT],
                    merged_at=_iso(merged_at),
                    tickets=_tickets(title),
                )
            )
        merged.sort(key=lambda m: m.merged_at)
        by_repo: dict[str, int] = {}
        by_class: dict[str, int] = {}
        drafts = 0
        prs = state.get("prs") or {}
        for key, record in prs.items():
            repo = key.rsplit("#", 1)[0]
            by_repo[repo] = by_repo.get(repo, 0) + 1
            cls = str(record.get("cls") or "unclassified")
            by_class[cls] = by_class.get(cls, 0) + 1
            drafts += bool((record.get("facts") or {}).get("draft"))
        queue = {
            "total": len(prs),
            "drafts": drafts,
            "by_repo": dict(sorted(by_repo.items(), key=lambda kv: -kv[1])),
            "by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        }
        return merged, queue, age

    @staticmethod
    def _clones(
        root: Path, start: datetime, end: datetime, now: datetime, errors: list[str]
    ) -> tuple[dict[str, list[ModelCloneCommit]], int]:
        out: dict[str, list[ModelCloneCommit]] = {}
        fetch_age = 0
        seen: set[Path] = set()
        for entry in sorted(root.iterdir(), key=lambda e: ("-" in e.name, e.name)):
            if (
                entry.name in {"omni_worktrees", "node_modules"}
                or not (entry / ".git").exists()
            ):
                continue
            if entry.resolve() in seen:
                continue
            seen.add(entry.resolve())
            try:
                ref = _git(
                    entry, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"
                ).strip()
                log = _git(
                    entry,
                    "log",
                    "--first-parent",
                    f"--since={_iso(start)}",
                    f"--until={_iso(end)}",
                    "--format=%s",
                    ref,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(f"clone {entry.name} unreadable: {exc}")
                continue
            commits = []
            for subject in (line for line in log.splitlines() if line.strip()):
                match = PR_PATTERN.search(subject)
                commits.append(
                    ModelCloneCommit(
                        repo=entry.name,
                        pr=int(match.group(1)) if match else None,
                        tickets=_tickets(subject),
                        subject=subject[:TEXT_LIMIT],
                    )
                )
            if commits:
                out[entry.name] = commits
            fetch = entry / ".git" / "FETCH_HEAD"
            if fetch.is_file():
                fetch_age = max(fetch_age, int(now.timestamp() - fetch.stat().st_mtime))
        return out, fetch_age

    @staticmethod
    def _ledger_rows(
        path: Path, start: datetime, errors: list[str]
    ) -> list[dict[str, str]]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            errors.append(f"ledger unreadable: {exc}")
            return []
        rows: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for line in reversed(lines):
            moment = _parse(line[:20])
            if moment is None:
                continue
            if moment < start - timedelta(hours=1):
                break
            parts = [p.strip() for p in line.split(" | ")]
            if len(parts) < 3 or parts[1] not in {"CLAIM", "TERMINAL", "STATUS"}:
                continue
            cells: dict[str, str] = {"ts": parts[0], "type": parts[1], "text": ""}
            for part in parts[2:]:
                key, sep, value = part.partition("=")
                if sep and re.fullmatch(r"[a-zA-Z_-]+", key) and key not in cells:
                    cells[key] = value if key == "codex" else value.split(" ")[0]
                else:
                    cells["text"] = (cells["text"] + " " + part).strip()
            identity = (cells["type"], cells.get("lane", ""), cells["ts"])
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(cells)
        rows.reverse()
        return rows

    def _lanes(
        self, path: Path, start: datetime, end: datetime, errors: list[str]
    ) -> dict[str, Any]:
        rows = self._ledger_rows(path, start, errors)
        in_window = []
        for row in rows:
            moment = _parse(row["ts"])
            if moment is not None and start < moment <= end:
                in_window.append(row)
        closed: list[ModelLaneClosed] = []
        controller_workers = 0
        for row in in_window:
            if row["type"] != "TERMINAL":
                continue
            if CONTROLLER_LANE.match(row.get("lane", "")):
                controller_workers += 1
                continue
            closed.append(
                ModelLaneClosed(
                    ts=row["ts"],
                    lane=row.get("lane", ""),
                    ticket=row.get("ticket", ""),
                    outcome=row.get("outcome", ""),
                    pr=row.get("pr", ""),
                    delegated=row.get("delegated", ""),
                    codex=bool(row.get("codex")),
                    text=row["text"][:TEXT_LIMIT],
                )
            )
        closed_claims = {r.get("closes-CLAIM") for r in rows if r["type"] == "TERMINAL"}
        closed_lanes = {r.get("lane") for r in rows if r["type"] == "TERMINAL"}
        started = [
            ModelLaneStarted(
                ts=r["ts"], lane=r.get("lane", ""), ticket=r.get("ticket", "")
            )
            for r in in_window
            if r["type"] == "CLAIM"
            and not CONTROLLER_LANE.match(r.get("lane", ""))
            and r["ts"] not in closed_claims
            and r.get("lane") not in closed_lanes
        ]
        m4 = [
            ModelM4Row(
                ts=r["ts"],
                row_type=r["type"],
                lane=r.get("lane", ""),
                ticket=r.get("ticket", ""),
                outcome=r.get("outcome", ""),
                text=r["text"][:TEXT_LIMIT],
            )
            for r in in_window
            if r["type"] in {"TERMINAL", "STATUS"}
            and M4_PATTERN.search(f"{r.get('lane', '')} {r['text']}")
        ]
        outcomes: dict[str, int] = {}
        for item in closed:
            key = item.outcome or "unrecorded"
            outcomes[key] = outcomes.get(key, 0) + 1
        return {
            "count": len(closed),
            "closed": tuple(closed[-LANE_LIST_LIMIT:]),
            "outcomes": outcomes,
            "controller": controller_workers,
            "started": tuple(started),
            "m4": tuple(m4),
        }

    @staticmethod
    def _friction(
        path: Path | None, errors: list[str]
    ) -> tuple[Literal["ok", "unavailable"], str, tuple[str, ...]]:
        if path is None:
            return "unavailable", "no friction data path given", ()
        if not path.is_file():
            return "unavailable", "the friction data file does not exist yet", ()
        try:
            top = json.loads(path.read_text(encoding="utf-8"))["top_dropped"]
            if not isinstance(top, list):
                raise TypeError("top_dropped is not a list")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"friction data unreadable: {exc}")
            return "unavailable", f"the friction data file did not parse: {exc}", ()
        return (
            "ok",
            "",
            tuple(json.dumps(item, sort_keys=True)[:TEXT_LIMIT] for item in top[:5]),
        )

    # -- commands ---------------------------------------------------------------

    def _facts(self, request: ModelOwnerSummaryRequest) -> ModelOwnerSummaryResult:
        now = (
            _parse(request.now)
            if request.now
            else datetime.now(UTC).replace(microsecond=0)
        )
        if now is None:
            return ModelOwnerSummaryResult(
                exit_code=2, message=f"--now is not an ISO instant: {request.now}"
            )
        if request.ledger is None:
            return ModelOwnerSummaryResult(
                exit_code=2, message="--ledger is required for facts"
            )
        try:
            start, end, source = self.resolve_window(
                now, request.since, request.until, self._state_file(request)
            )
        except ValueError as exc:
            return ModelOwnerSummaryResult(exit_code=2, message=str(exc))
        errors: list[str] = []
        watcher_path = request.watcher_state or (
            request.root / ".claude_scratch" / "pr-watcher" / "state.json"
        )
        merged, queue, age = self._watcher(watcher_path, start, end, now, errors)
        clones, fetch_age = self._clones(request.root, start, end, now, errors)
        lanes = self._lanes(request.ledger, start, end, errors)
        friction_status, friction_reason, friction_top = self._friction(
            request.friction_data, errors
        )
        watcher_pairs = {(m.repo, m.number) for m in merged}
        clone_only = tuple(
            c
            for commits in clones.values()
            for c in commits
            if (c.repo, c.pr) not in watcher_pairs
        )
        by_repo: dict[str, int] = {}
        for change in merged:
            by_repo[change.repo] = by_repo.get(change.repo, 0) + 1
        facts = ModelOwnerSummaryFacts(
            generated_at=_iso(now),
            window_since=_iso(start),
            window_until=_iso(end),
            window_source=source,
            merged_changes=tuple(merged),
            clone_only_commits=clone_only,
            merged_by_repo=dict(sorted(by_repo.items(), key=lambda kv: -kv[1])),
            clone_commits_by_repo={r: len(c) for r, c in clones.items()},
            watcher_state_age_s=age,
            clone_fetch_age_max_s=fetch_age,
            open_total=int(queue.get("total", 0)),
            open_drafts=int(queue.get("drafts", 0)),
            open_by_repo=queue.get("by_repo", {}),
            open_by_class=queue.get("by_class", {}),
            lanes_closed_count=lanes["count"],
            lanes_closed=lanes["closed"],
            lanes_closed_by_outcome=lanes["outcomes"],
            controller_worker_lanes_closed=lanes["controller"],
            lanes_started_not_closed=lanes["started"],
            m4_rows=lanes["m4"],
            friction_status=friction_status,
            friction_reason=friction_reason,
            friction_top_dropped=friction_top,
            errors=tuple(errors),
        )
        return ModelOwnerSummaryResult(
            exit_code=0,
            message=f"{len(merged)} merges, {lanes['count']} lanes closed",
            facts=facts,
        )

    @staticmethod
    def check_draft(text: str, facts: ModelOwnerSummaryFacts) -> list[str]:
        """Return every rule the draft breaks; an empty list is a pass."""
        problems: list[str] = []
        body = text.rstrip().splitlines()
        if not body or not FOOTER_PATTERN.match(body[-1].strip()):
            problems.append(
                "the last line must be the footer: (via <first name>'s agent)"
            )
        words = len(text.split())
        if words > MAX_WORDS:
            problems.append(f"{words} words; at most {MAX_WORDS}")
        if SECRET_PATTERN.search(text):
            problems.append("the draft holds a token-shaped string")
        if HOME_PATH_PATTERN.search(text):
            problems.append("the draft names a home or volume path")
        corpus = facts.model_dump_json()
        cited = _tickets(text)
        if not cited and (facts.merged_changes or facts.clone_only_commits):
            problems.append("the draft cites no ticket number")
        problems.extend(f"{t} is not in the facts" for t in cited if t not in corpus)
        pairs = {(m.repo, m.number) for m in facts.merged_changes}
        pairs |= {(c.repo, c.pr) for c in facts.clone_only_commits if c.pr}
        for repo, number in REPO_PR_PATTERN.findall(text):
            if (repo, int(number)) not in pairs:
                problems.append(
                    f"{repo}#{number} is not a merge in the facts; cite merged changes only"
                )
        if "M4" not in text:
            problems.append("the draft has no M4 board line")
        if facts.friction_status != "ok" and re.search(r"(?i)\bfriction\b", text):
            problems.append(
                "the draft has a friction section but the friction data is unavailable"
            )
        return problems

    def _check(self, request: ModelOwnerSummaryRequest) -> ModelOwnerSummaryResult:
        if request.draft is None or request.facts_file is None:
            return ModelOwnerSummaryResult(
                exit_code=2, message="--draft and --facts are required"
            )
        try:
            facts = ModelOwnerSummaryFacts.model_validate_json(
                request.facts_file.read_text(encoding="utf-8")
            )
            text = request.draft.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            return ModelOwnerSummaryResult(exit_code=2, message=str(exc))
        problems = self.check_draft(text, facts)
        if problems:
            return ModelOwnerSummaryResult(
                exit_code=1,
                message=f"{len(problems)} problem(s); rewrite the draft",
                problems=tuple(problems),
            )
        return ModelOwnerSummaryResult(exit_code=0, message="PASS")
