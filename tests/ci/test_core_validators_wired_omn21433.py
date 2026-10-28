# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-21433 T2.1: omnimarket consumes omnibase_core's validators by hook id.

The rules live in omnibase_core. This repository owns only the wiring: each
validator is a hook of the shared core hook repository at a dev-merge tag, the
validators that judge against a baseline read a baseline that only shrinks, and
the same hooks run in CI as a job that CI Summary registers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PRECOMMIT = REPO_ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CI_SUMMARY_GATE = REPO_ROOT / "scripts" / "ci" / "ci_summary_gate.py"

CORE_URL = "https://github.com/OmniNode-ai/omnibase_core"
SECRETS_URL = "https://github.com/Yelp/detect-secrets"
DEV_MERGE_TAG = re.compile(r"dev-\d{14}-[0-9a-f]{12}")
CI_JOB_ID = "core-validators"
CI_JOB_NAME = "Core Validators (OMN-21433)"

CORE_HOOKS = (
    "check-no-utcnow",
    "check-no-raw-sqlite3",
    "check-no-direct-kafka-producer",
    "check-local-paths-compute",
    "spdx-headers-check",
    "pydantic-extra-forbid",
    "check-no-backward-compat",
    "check-hardcoded-model-config-compute",
)
CI_HOOKS = (*CORE_HOOKS, "detect-secrets")
# Hooks that judge against a committed baseline, with the baseline's path.
BASELINES = {
    "pydantic-extra-forbid": ".onex_ratchets/extra_forbid_baseline.yaml",
    "check-hardcoded-model-config-compute": "config/hardcoded_model_config_baseline.yaml",
    "detect-secrets": ".secrets.baseline",
}


def _repos() -> list[dict[str, Any]]:
    loaded = yaml.safe_load(PRECOMMIT.read_text(encoding="utf-8"))
    repos = loaded["repos"]
    assert isinstance(repos, list)
    return repos


def _hook(url: str, hook_id: str) -> tuple[str, dict[str, Any]]:
    found = [
        (str(repo["rev"]), hook)
        for repo in _repos()
        if repo.get("repo") == url
        for hook in repo["hooks"]
        if hook.get("id") == hook_id
    ]
    assert len(found) == 1, f"{hook_id}: expected one hook of {url}, found {len(found)}"
    return found[0]


@pytest.mark.parametrize("hook_id", CORE_HOOKS)
def test_core_hook_is_consumed_at_a_dev_merge_tag(hook_id: str) -> None:
    rev, _ = _hook(CORE_URL, hook_id)
    assert DEV_MERGE_TAG.fullmatch(rev), (
        f"{hook_id}: rev {rev!r} is not a dev-merge tag"
    )


def test_core_hooks_share_one_tag() -> None:
    revs = {_hook(CORE_URL, hook_id)[0] for hook_id in CORE_HOOKS}
    assert len(revs) == 1, f"core validators pinned at several tags: {sorted(revs)}"


@pytest.mark.parametrize(("hook_id", "baseline"), sorted(BASELINES.items()))
def test_baselined_hook_reads_its_committed_baseline(
    hook_id: str, baseline: str
) -> None:
    url = SECRETS_URL if hook_id == "detect-secrets" else CORE_URL
    _, hook = _hook(url, hook_id)
    args = [str(arg) for arg in hook.get("args", [])]
    assert "--baseline" in args, f"{hook_id} does not pass --baseline"
    assert args[args.index("--baseline") + 1] == baseline
    assert (REPO_ROOT / baseline).is_file(), f"{baseline} is not committed"


def test_secrets_hook_does_not_call_the_vendor_apis() -> None:
    _, hook = _hook(SECRETS_URL, "detect-secrets")
    assert "--no-verify" in [str(arg) for arg in hook["args"]]


def _ci_job() -> dict[str, Any]:
    jobs = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    job = jobs[CI_JOB_ID]
    assert isinstance(job, dict)
    return job


def test_ci_job_runs_every_hook_unconditionally_on_a_hosted_runner() -> None:
    job = _ci_job()
    assert job["name"] == CI_JOB_NAME
    assert job["runs-on"] == "ubuntu-latest"
    assert "needs" not in job
    assert "if" not in job
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    for hook_id in CI_HOOKS:
        assert hook_id in runs, f"ci.yml {CI_JOB_ID} does not run {hook_id}"
    assert "--all-files" in runs
    assert "POSITIVE CONTROL FAILED" in runs


def test_ci_summary_registers_the_job() -> None:
    assert f'"{CI_JOB_NAME}",' in CI_SUMMARY_GATE.read_text(encoding="utf-8")
