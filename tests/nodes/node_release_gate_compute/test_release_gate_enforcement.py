# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-21433: the enforcement declared as code matches what the gate expects."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from omnimarket.nodes.node_release_gate_rulesets_effect.handlers import (
    handler_release_gate_rulesets as apply,
)
from omnimarket.nodes.node_release_gate_rulesets_effect.models.model_release_gate_rulesets import (
    ModelReleaseGateRulesetsRequest,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
RULESETS = ROOT / ".github" / "rulesets"
WORKFLOWS = ROOT / ".github" / "workflows"


def _ruleset(name: str) -> dict[str, Any]:
    return json.loads((RULESETS / f"{name}.json").read_text())


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text())


# tag protection ------------------------------------------------------------------


def test_tag_ruleset_blocks_create_update_delete_for_release_tags_only() -> None:
    ruleset = _ruleset("release-tags")
    assert ruleset["target"] == "tag"
    assert ruleset["enforcement"] == "active"
    assert {rule["type"] for rule in ruleset["rules"]} == {
        "creation",
        "update",
        "deletion",
    }
    pattern = ruleset["conditions"]["ref_name"]["include"][0]
    assert fnmatch.fnmatch("refs/tags/v0.5.0", pattern)
    # The dev merge tags carry no release and stay creatable by the workflow token.
    assert not fnmatch.fnmatch("refs/tags/dev-20261027120000-abcdef012345", pattern)
    assert not fnmatch.fnmatch("refs/tags/lab/dev/20261027-abc", pattern)


def test_tag_ruleset_bypass_is_exactly_the_train_app() -> None:
    actors = _ruleset("release-tags")["bypass_actors"]
    assert actors == [
        {
            "actor_id": "${RELEASE_TRAIN_APP_ID}",
            "actor_type": "Integration",
            "bypass_mode": "always",
        }
    ]


def test_required_check_ruleset_requires_the_gate_job_name() -> None:
    ruleset = _ruleset("release-gate-required-check")
    (rule,) = ruleset["rules"]
    contexts = [c["context"] for c in rule["parameters"]["required_status_checks"]]
    assert contexts == [_workflow("release-gate.yml")["jobs"]["release-gate"]["name"]]
    assert ruleset["bypass_actors"] == []


# the apply script ------------------------------------------------------------------


def test_apply_refuses_without_the_train_app_id() -> None:
    with pytest.raises(ValueError, match="RELEASE_TRAIN_APP_ID"):
        apply.load_declarations(RULESETS, "")
    with pytest.raises(ValueError, match="RELEASE_TRAIN_APP_ID"):
        apply.load_declarations(RULESETS, "not-a-number")


def test_apply_substitutes_the_app_id_as_a_number() -> None:
    declared = {d["name"]: d for d in apply.load_declarations(RULESETS, "12345")}
    assert declared["release-tags"]["bypass_actors"][0]["actor_id"] == 12345


def test_plan_writes_nothing_and_apply_creates_then_updates() -> None:
    calls: list[tuple[str, str]] = []
    existing = [{"id": 9, "name": "release-tags"}]

    def api(method: str, path: str, body: Any) -> Any:
        calls.append((method, path))
        return existing if method == "GET" else None

    def run(*, do_apply: bool) -> tuple[str, ...]:
        request = ModelReleaseGateRulesetsRequest(
            repo="o/r", directory=str(RULESETS), train_app_id="12345", apply=do_apply
        )
        return apply.HandlerReleaseGateRulesets(api).handle(request).lines

    planned = run(do_apply=False)
    assert calls == [("GET", "repos/o/r/rulesets")]
    assert planned == (
        "PLAN create release-gate-required-check",
        "PLAN update release-tags",
    )
    calls.clear()
    applied = run(do_apply=True)
    assert applied[0].startswith("APPLY create")
    assert calls == [
        ("GET", "repos/o/r/rulesets"),
        ("POST", "repos/o/r/rulesets"),
        ("PUT", "repos/o/r/rulesets/9"),
    ]


# the workflows ---------------------------------------------------------------------


def test_gate_workflow_judges_with_the_default_branch_never_the_pr_head() -> None:
    text = (WORKFLOWS / "release-gate.yml").read_text()
    workflow = yaml.safe_load(text)
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert "pull_request_target" in triggers
    assert "pull_request" not in triggers
    assert {"workflow_call", "merge_group"} <= set(triggers)
    # The tag push and published release audit lives in release-audit.yml, which
    # calls this gate, so this file reports one context on pull requests and the queue.
    assert not {"push", "release"} & set(triggers)
    audit = _workflow("release-audit.yml")
    audit_triggers = audit[True] if True in audit else audit["on"]
    assert {"push", "release"} <= set(audit_triggers)
    assert audit["jobs"]["audit"]["uses"] == "./.github/workflows/release-gate.yml"
    assert "github.event.repository.default_branch" in text
    assert "pull_request.head" not in text
    # A fallback to the workflow token on the write path is the OMN-18273 defect;
    # this job only reads, and says so where it falls back.
    assert "secrets.GITHUB_TOKEN" not in text


@pytest.mark.parametrize(
    ("workflow", "first_job"),
    [("release.yml", "release"), ("release-cut.yml", "decide")],
)
def test_release_workflows_cannot_start_without_the_gate(
    workflow: str, first_job: str
) -> None:
    jobs = _workflow(workflow)["jobs"]
    assert jobs["release-gate"]["uses"] == "./.github/workflows/release-gate.yml"
    assert jobs[first_job]["needs"] in ("release-gate", ["release-gate"])
    # No job that can publish or tag sits outside the gate's dependency chain.
    for name, job in jobs.items():
        if name == "release-gate":
            continue
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        assert needs, f"{workflow}:{name} has no needs and would run ungated"
