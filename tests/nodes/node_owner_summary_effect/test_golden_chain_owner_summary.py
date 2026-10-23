# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Golden and error chains for the owner summary node: facts, shape check and send watermark."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from omnimarket.nodes.node_owner_summary_effect.__main__ import main
from omnimarket.nodes.node_owner_summary_effect.handlers import HandlerOwnerSummary
from omnimarket.nodes.node_owner_summary_effect.models import (
    ModelCloneCommit,
    ModelMergedChange,
    ModelOwnerSummaryFacts,
    ModelOwnerSummaryRequest,
)

pytestmark = pytest.mark.unit

NODE = (
    Path(__file__).resolve().parents[3]
    / "src/omnimarket/nodes/node_owner_summary_effect"
)

NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
HANDLER = HandlerOwnerSummary()
GOOD = (
    "Summary. omnimarket#12 landed OMN-100 and omniclaude#7 landed OMN-200.\n"
    "*M4 board*\n- nothing moved as of the last lane report.\n"
    "(via Sam's agent)\n"
)


def _facts() -> ModelOwnerSummaryFacts:
    return ModelOwnerSummaryFacts(
        generated_at="2026-10-24T12:00:00Z",
        window_since="2026-10-24T00:00:00Z",
        window_until="2026-10-24T12:00:00Z",
        window_source="--since",
        merged_changes=(
            ModelMergedChange(
                repo="omnimarket",
                number=12,
                title="t",
                merged_at="2026-10-24T01:00:00Z",
                tickets=("OMN-100",),
            ),
        ),
        clone_only_commits=(
            ModelCloneCommit(
                repo="omniclaude", pr=7, tickets=("OMN-200",), subject="s"
            ),
        ),
    )


def test_good_draft_passes() -> None:
    assert HANDLER.check_draft(GOOD, _facts()) == []


def test_missing_footer_fails() -> None:
    problems = HANDLER.check_draft(
        GOOD.replace("(via Sam's agent)", "thanks"), _facts()
    )
    assert any("footer" in p for p in problems)


def test_fabricated_ticket_and_pr_fail() -> None:
    draft = GOOD.replace("OMN-100", "OMN-999").replace("omnimarket#12", "omnimarket#13")
    problems = HANDLER.check_draft(draft, _facts())
    assert any("OMN-999" in p for p in problems)
    assert any("omnimarket#13" in p for p in problems)


def test_secret_path_and_length_fail() -> None:
    home = "/".join(["", "Users", "someone", "x"])
    token = "xoxb" + "-1234567890-abcdef"
    draft = GOOD + f"token {token} {home} " + "word " * 500 + "\n(via Sam's agent)\n"
    text = "\n".join(HANDLER.check_draft(draft, _facts()))
    assert "token-shaped" in text
    assert "home or volume path" in text
    assert "words; at most" in text


def test_no_m4_line_and_unavailable_friction_fail() -> None:
    draft = GOOD.replace("M4", "board") + "Friction: x\n(via Sam's agent)\n"
    problems = HANDLER.check_draft(draft, _facts())
    assert any("M4" in p for p in problems)
    assert any("friction" in p for p in problems)


def test_window_uses_last_sent_and_caps(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"last_sent_until": "2026-10-24T05:00:00Z"}))
    start, end, source = HANDLER.resolve_window(NOW, "", "", state)
    assert (start, end, source) == (
        datetime(2026, 10, 24, 5, tzinfo=UTC),
        NOW,
        "last-sent",
    )
    state.write_text(json.dumps({"last_sent_until": "2026-10-01T00:00:00Z"}))
    start, _, source = HANDLER.resolve_window(NOW, "", "", state)
    assert start == NOW - timedelta(hours=48)
    assert "capped" in source
    start, _, source = HANDLER.resolve_window(NOW, "", "", tmp_path / "absent.json")
    assert start == NOW - timedelta(hours=12)
    assert source.startswith("default")
    with pytest.raises(ValueError, match="not an ISO instant"):
        HANDLER.resolve_window(NOW, "not a time", "", state)


def _write_world(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    watcher = home / ".claude_scratch" / "pr-watcher"
    watcher.mkdir(parents=True)
    (watcher / "state.json").write_text(
        json.dumps(
            {
                "last_tick": "2026-10-24T11:59:00Z",
                "merges": {
                    "a#1": {
                        "repo": "a",
                        "number": 1,
                        "title": "fix(OMN-5): x (#1)",
                        "merged_at": "2026-10-24T10:00:00Z",
                        "author": "someone",
                    },
                    "a#2": {
                        "repo": "a",
                        "number": 2,
                        "title": "old",
                        "merged_at": "2026-10-20T10:00:00Z",
                        "author": "someone",
                    },
                },
                "prs": {
                    "a#3": {"cls": "red", "facts": {"draft": True}},
                    "b#4": {"cls": "red", "facts": {}},
                },
            }
        )
    )
    ledger = tmp_path / "ledger.md"
    ledger.write_text(
        "\n".join(
            [
                "2026-10-24T08:00:00Z | CLAIM | lane=alpha | ticket=OMN-1 | note",
                "2026-10-24T08:30:00Z | CLAIM | lane=beta | ticket=OMN-2 | note",
                "2026-10-24T09:00:00Z | TERMINAL | lane=alpha | ticket=OMN-1 | outcome=done"
                " | closes-CLAIM=2026-10-24T08:00:00Z | delegated=2 codex=abc | all done",
                "2026-10-24T09:10:00Z | TERMINAL | lane=landing-L123 | outcome=handed-off | ended",
                "2026-10-24T10:00:00Z | STATUS | lane=m4-c16-diag | ticket=OMN-3 | C16 red again",
                "2026-10-20T10:00:00Z | TERMINAL | lane=old | outcome=done | out of window",
            ]
        )
    )
    return home, ledger


def test_facts_merges_queue_lanes_and_no_authors(tmp_path: Path) -> None:
    home, ledger = _write_world(tmp_path)
    result = HANDLER.handle(
        ModelOwnerSummaryRequest(
            command="facts",
            root=home,
            ledger=ledger,
            since="2026-10-24T00:00:00Z",
            now="2026-10-24T12:00:00Z",
        )
    )
    assert result.exit_code == 0
    facts = result.facts
    assert facts is not None
    assert [(m.repo, m.number, m.tickets) for m in facts.merged_changes] == [
        ("a", 1, ("OMN-5",))
    ]
    assert "someone" not in facts.model_dump_json()
    assert (facts.open_total, facts.open_drafts) == (2, 1)
    assert facts.open_by_class == {"red": 2}
    assert [c.lane for c in facts.lanes_closed] == ["alpha"]
    assert facts.lanes_closed[0].codex is True
    assert facts.controller_worker_lanes_closed == 1
    assert [s.lane for s in facts.lanes_started_not_closed] == ["beta"]
    assert [r.lane for r in facts.m4_rows] == ["m4-c16-diag"]
    assert facts.friction_status == "unavailable"
    assert facts.errors == ()


def test_facts_names_an_unreadable_source(tmp_path: Path) -> None:
    result = HANDLER.handle(
        ModelOwnerSummaryRequest(
            command="facts",
            root=tmp_path,
            ledger=tmp_path / "missing.md",
            since="2026-10-24T00:00:00Z",
            now="2026-10-24T12:00:00Z",
        )
    )
    assert result.facts is not None
    assert any("watcher state unreadable" in e for e in result.facts.errors)
    assert any("ledger unreadable" in e for e in result.facts.errors)


def test_friction_unavailable_then_ok(tmp_path: Path) -> None:
    errors: list[str] = []
    assert HANDLER._friction(None, errors)[0] == "unavailable"
    assert HANDLER._friction(tmp_path / "no.json", errors)[0] == "unavailable"
    data = tmp_path / "d.json"
    data.write_text(json.dumps({"top_dropped": [1, 2, 3, 4, 5, 6]}))
    status, _, top = HANDLER._friction(data, errors)
    assert status == "ok"
    assert top == ("1", "2", "3", "4", "5")
    data.write_text("{not json")
    assert HANDLER._friction(data, errors)[0] == "unavailable"
    assert errors


def test_cli_facts_check_and_mark_sent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home, ledger = _write_world(tmp_path)
    assert (
        main(
            [
                "facts",
                "--omni-home",
                str(home),
                "--ledger",
                str(ledger),
                "--since",
                "2026-10-24T00:00:00Z",
                "--now",
                "2026-10-24T12:00:00Z",
            ]
        )
        == 0
    )
    facts_text = capsys.readouterr().out
    assert json.loads(facts_text)["window_source"] == "--since"
    (tmp_path / "facts.json").write_text(facts_text)
    (tmp_path / "draft.md").write_text(
        "a#1 landed OMN-5. M4 unchanged as of the last report.\n(via Sam's agent)\n"
    )
    checked = [
        "check",
        "--draft",
        str(tmp_path / "draft.md"),
        "--facts",
        str(tmp_path / "facts.json"),
    ]
    assert main(checked) == 0
    (tmp_path / "draft.md").write_text(
        "a#9 landed OMN-9. M4 unchanged.\n(via Sam's agent)\n"
    )
    assert main(checked) == 1
    assert "FAIL a#9" in capsys.readouterr().out
    assert (
        main(["mark-sent", "--omni-home", str(home), "--until", "2026-10-24T12:00:00Z"])
        == 0
    )
    state = json.loads(
        (home / ".onex_state" / "owner-summary" / "state.json").read_text()
    )
    assert state["last_sent_until"] == "2026-10-24T12:00:00Z"
    assert main(["mark-sent", "--omni-home", str(home), "--until", "garbage"]) == 2


def test_contract_declares_its_topics_and_resolvable_handler() -> None:
    contract = yaml.safe_load((NODE / "contract.yaml").read_text(encoding="utf-8"))
    bus = contract["event_bus"]
    assert bus["subscribe_topics"] == [
        "onex.cmd.omnimarket.owner-summary-command-requested.v1"
    ]
    assert bus["publish_topics"] == [
        "onex.evt.omnimarket.owner-summary-command-completed.v1"
    ]
    assert contract["runtime_dispatch"]["command_topic"] == bus["subscribe_topics"][0]
    assert contract["terminal_event"] == bus["publish_topics"][0]
    assert contract["node_type"] == "effect"
