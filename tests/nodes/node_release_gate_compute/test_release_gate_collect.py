# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-21433: the collector turns GitHub API facts into a gate request.

``fetch`` is injected, so these run against recorded-shape payloads. The point
under test is which API field each fact comes from, because an agent controls
some of them (a commit's author email) and not others (the push actor).
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
import yaml
from release_gate_builders import HEAD, IDENTITIES, NOW, POLICY_FILE, TRAIN, load_policy

from omnimarket.handlers.handler_release_gate import HandlerReleaseGate
from omnimarket.nodes.node_release_gate_collect_effect.handlers.handler_release_gate_collect import (
    NotFoundError,
    collect_request,
    load_identities,
)

pytestmark = pytest.mark.unit

POLICY = load_policy()
REPO = "OmniNode-ai/omnimarket"
PATCH_BUMP = '@@ -3,4 +3,4 @@\n name = "x"\n-version = "0.4.9"\n+version = "0.5.0"\n'
PATCH_DEP = '@@ -9,3 +9,3 @@\n-  "pydantic>=2.10",\n+  "pydantic>=2.11",\n'


def api(
    *,
    author: tuple[str, str] = (TRAIN, "Bot"),
    push_actor: str = TRAIN,
    commit_logins: tuple[str | None, ...] = (TRAIN,),
    patch: str = PATCH_BUMP,
    labeled: tuple[tuple[str, str, str, bool], ...] = (),
    labels: tuple[str, ...] = (),
    grants: list[dict[str, Any]] | None = None,
    title: str = "chore: release omnimarket 0.5.0",
    head_ref: str = "automation/release-0.5.0-omn-18595",
):
    grant_doc = yaml.safe_dump({"grants": grants or []}).encode()

    def fetch(path: str) -> Any:
        if path == f"repos/{REPO}/pulls/77":
            return {
                "number": 77,
                "title": title,
                "head": {"ref": head_ref, "sha": HEAD},
                "base": {"ref": "dev"},
                "user": {"login": author[0], "type": author[1]},
                "labels": [{"name": n} for n in labels],
            }
        if path == f"repos/{REPO}/pulls/77/files":
            return [{"filename": "pyproject.toml", "patch": patch}]
        if path == f"repos/{REPO}/pulls/77/commits":
            return [
                {
                    "author": {"login": c} if c else None,
                    "committer": {"login": c} if c else None,
                }
                for c in commit_logins
            ]
        if path == f"repos/{REPO}/issues/77/timeline":
            return [
                {
                    "event": "labeled",
                    "label": {"name": name},
                    "actor": {"login": login, "type": typ},
                    "performed_via_github_app": {"id": 1} if app else None,
                    "created_at": "2026-10-27T11:55:00Z",
                }
                for name, login, typ, app in labeled
            ]
        if path.startswith(f"repos/{REPO}/activity?"):
            assert f"ref=refs/heads/{head_ref}" in path
            return [
                {
                    "after": "0" * 40,
                    "actor": {"login": "someone"},
                    "timestamp": "2026-10-27T10:00:00Z",
                },
                {
                    "after": HEAD,
                    "actor": {"login": push_actor},
                    "timestamp": "2026-10-27T11:30:00Z",
                },
            ]
        if path.startswith(
            "repos/OmniNode-ai/onex_change_control/contents/grants/release_grants.yaml"
        ):
            assert "ref=main" in path
            return {
                "content": base64.b64encode(grant_doc).decode(),
                "encoding": "base64",
            }
        raise NotFoundError(path)

    return fetch


def pr_event(sender: str = "lab-bot", typ: str = "User") -> dict[str, Any]:
    return {"pull_request": {"number": 77}, "sender": {"login": sender, "type": typ}}


def collect(fetch, event_name="pull_request", payload=None, identities=IDENTITIES):
    return collect_request(
        repo=REPO,
        event_name=event_name,
        payload=payload or pr_event(),
        fetch=fetch,
        policy=POLICY,
        identities=identities,
        now=NOW,
    )


def test_train_pr_facts_are_read_from_the_pull_request_and_activity() -> None:
    req = collect(api())
    facts = req.pull_request
    assert facts is not None
    assert (facts.author_login, facts.author_type) == (TRAIN, "Bot")
    assert facts.head_push_actor_login == TRAIN
    assert facts.version_changed is True
    assert HandlerReleaseGate().handle(req).verdict == "TRAIN"


def test_push_actor_is_the_activity_actor_not_the_commit_author() -> None:
    """Falsifier: an agent sets the git author to the App's address and every
    commit then lists the App as author; the activity actor is still the agent."""
    req = collect(api(push_actor="lab-bot"))
    result = HandlerReleaseGate().handle(req)
    assert result.verdict == "REFUSED"


def test_no_activity_for_the_head_sha_leaves_push_actor_empty() -> None:
    fetch = api()

    def no_activity(path: str) -> Any:
        return [] if "/activity?" in path else fetch(path)

    facts = collect(no_activity).pull_request
    assert facts is not None
    assert facts.head_push_actor_login == ""


def test_unlinked_commit_author_is_not_the_train() -> None:
    facts = collect(api(commit_logins=(TRAIN, None))).pull_request
    assert facts is not None
    assert TRAIN not in {
        c for c in facts.commit_author_logins if c.startswith("unlinked")
    }
    assert any(c.startswith("unlinked") for c in facts.commit_author_logins)


def test_dependency_edit_is_not_a_version_change() -> None:
    facts = collect(api(patch=PATCH_DEP)).pull_request
    assert facts is not None
    assert facts.version_changed is False


def test_label_events_carry_actor_type_and_app_flag() -> None:
    fetch = api(
        author=("lab-bot", "User"),
        push_actor="lab-bot",
        commit_logins=("lab-bot",),
        labels=("release-grant",),
        labeled=(
            ("release-grant", "grantor-op", "User", False),
            ("other", "x", "User", False),
        ),
        title="chore: release omnimarket 0.5.1",
        head_ref="OMN-1-release",
    )
    facts = collect(fetch).pull_request
    assert facts is not None
    assert [
        (e.label, e.actor_login, e.actor_type, e.performed_via_app)
        for e in facts.label_events
    ] == [
        ("release-grant", "grantor-op", "User", False),
        ("other", "x", "User", False),
    ]
    assert facts.labels == ("release-grant",)


def test_grants_are_read_from_the_source_ref_and_missing_file_means_none() -> None:
    record = {
        "grant_id": "rg-1",
        "repo": REPO,
        "pr_number": 77,
        "head_sha": HEAD,
        "requested_by": "lab-bot",
        "approved_by": "grantor-op",
        "expires_at": "2026-10-27T16:00:00Z",
    }
    assert [g.grant_id for g in collect(api(grants=[record])).grants] == ["rg-1"]

    base = api()

    def absent(path: str) -> Any:
        if "release_grants.yaml" in path:
            raise NotFoundError(path)
        return base(path)

    assert collect(absent).grants == ()


def test_tag_push_reads_the_pusher_and_the_ref() -> None:
    payload = {
        "ref": "refs/tags/v0.5.0",
        "sender": {"login": "lab-bot", "type": "User"},
    }
    req = collect(api(), event_name="push", payload=payload)
    assert (req.event, req.tag, req.actor_login) == ("tag_push", "v0.5.0", "lab-bot")
    assert HandlerReleaseGate().handle(req).verdict == "REFUSED"


def test_branch_push_is_not_a_release_event() -> None:
    payload = {"ref": "refs/heads/dev", "sender": {"login": "x", "type": "User"}}
    with pytest.raises(ValueError, match="not a release event"):
        collect(api(), event_name="push", payload=payload)


def test_dispatch_and_release_events_map() -> None:
    sender = {"sender": {"login": TRAIN, "type": "Bot"}}
    assert (
        collect(api(), event_name="workflow_dispatch", payload=sender).event
        == "dispatch"
    )
    assert (
        collect(api(), event_name="release", payload=sender).event
        == "release_published"
    )


def test_identities_file_absent_or_missing_is_empty_and_present_is_read(
    tmp_path,
) -> None:
    assert load_identities(None).grantors == {}
    assert load_identities(str(tmp_path / "nope.yaml")).grantors == {}
    path = tmp_path / "ids.yaml"
    path.write_text(
        yaml.safe_dump({"grantors": {"operator": ["a"]}, "agent_logins": ["b"]})
    )
    ids = load_identities(str(path))
    assert ids.grantors == {"operator": ("a",)}
    assert ids.agent_logins == ("b",)


def test_unreadable_grants_do_not_break_a_non_release_pr_and_refuse_a_release_pr() -> (
    None
):
    import subprocess

    base = api(
        author=("lab-bot", "User"),
        push_actor="lab-bot",
        commit_logins=("lab-bot",),
        title="chore: release omnimarket 0.5.1",
        head_ref="OMN-1-release",
    )

    def forbidden(path: str) -> Any:
        if "release_grants.yaml" in path:
            raise subprocess.CalledProcessError(1, ["gh"])
        return base(path)

    assert collect(forbidden).grants == ()
    assert HandlerReleaseGate().handle(collect(forbidden)).verdict == "REFUSED"


def _completed(stdout: str = "", stderr: str = "", code: int = 0):
    import subprocess

    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_gh_fetch_joins_pages_and_maps_404(monkeypatch) -> None:
    from omnimarket.nodes.node_release_gate_collect_effect.handlers import (
        handler_release_gate_collect as module,
    )

    monkeypatch.setattr(
        module.subprocess, "run", lambda *_a, **_k: _completed('[{"a":1}]\n[{"a":2}]\n')
    )
    assert module.gh_fetch("repos/o/r/pulls") == [{"a": 1}, {"a": 2}]

    monkeypatch.setattr(
        module.subprocess, "run", lambda *_a, **_k: _completed("", "HTTP 404", 1)
    )
    with pytest.raises(NotFoundError):
        module.gh_fetch("repos/o/r/contents/x")


def test_main_exits_3_when_facts_cannot_be_read(tmp_path, monkeypatch, capsys) -> None:
    from omnimarket.nodes.node_release_gate_collect_effect.handlers import (
        handler_release_gate_collect as module,
    )

    def boom(path: str) -> Any:
        raise RuntimeError("api down")

    monkeypatch.setattr(module, "gh_fetch", boom)
    from omnimarket.nodes.node_release_gate_collect_effect.__main__ import main

    event = tmp_path / "event.json"
    event.write_text(
        '{"pull_request": {"number": 77}, "sender": {"login": "x", "type": "User"}}'
    )
    code = main(
        [
            "--repo",
            REPO,
            "--event-name",
            "pull_request",
            "--event-path",
            str(event),
            "--policy",
            str(POLICY_FILE),
        ]
    )
    assert code == 3
    assert "cannot collect facts" in capsys.readouterr().err
