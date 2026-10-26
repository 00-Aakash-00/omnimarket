# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""OMN-19450 AC2: the five-fault suite runs on a schedule and publishes its result."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = (
    REPO_ROOT / ".github" / "workflows" / "delegation-fault-suite-nightly.yml"
)
SUITE_DIR = "tests/chains/fault_suite"
JUNIT_REPORT = "delegation_fault_suite_junit.xml"


def _steps() -> list[dict[str, Any]]:
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = data["jobs"]["fault-suite"]["steps"]
    return steps


def test_the_suite_runs_on_a_nightly_schedule_against_dev() -> None:
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    # PyYAML 1.1 resolves the bare `on:` key to the boolean True.
    triggers = data.get("on", data.get(True))
    assert isinstance(triggers, dict)
    assert [entry["cron"] for entry in triggers["schedule"]], "no cron schedule"
    assert "pull_request" not in triggers, "the nightly must not be a PR context"
    checkout = next(
        step
        for step in _steps()
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["ref"] == "dev"


def test_the_run_step_runs_the_fault_suite_into_a_junit_report() -> None:
    run = next(
        str(step["run"]) for step in _steps() if SUITE_DIR in str(step.get("run", ""))
    )
    assert "uv run pytest" in run
    assert f"--junitxml={JUNIT_REPORT}" in run
    assert (REPO_ROOT / SUITE_DIR).is_dir()


def test_the_result_is_published_as_summary_and_artifact() -> None:
    steps = _steps()
    summary = next(
        step for step in steps if "GITHUB_STEP_SUMMARY" in str(step.get("run", ""))
    )
    assert summary.get("if") == "always()"
    assert JUNIT_REPORT in str(summary["run"])
    upload = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload.get("if") == "always()"
    assert upload["with"]["path"] == JUNIT_REPORT
