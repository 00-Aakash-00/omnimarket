# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Internal sibling pins reuse the core guard and gate admission (OMN-21478)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CONTEXT = "internal-sibling-pins / Internal Sibling Pins"
REUSABLE = "OmniNode-ai/omnibase_core/.github/workflows/internal-sibling-pins.yml@"
pytestmark = pytest.mark.unit


def test_workflow_calls_core_at_a_full_sha_and_is_required() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/internal-sibling-pins.yml").read_text()
    )
    assert set(workflow["jobs"]) == {"internal-sibling-pins"}
    caller = workflow["jobs"]["internal-sibling-pins"]
    assert set(caller) == {"uses"}
    assert caller["uses"].startswith(REUSABLE)
    assert re.fullmatch(r"[0-9a-f]{40}", caller["uses"].removeprefix(REUSABLE))

    manifest = yaml.safe_load((ROOT / ".github/required-checks.yaml").read_text())
    rows = [row for row in manifest["gates"] if row["name"] == CONTEXT]
    assert len(rows) == 1
    assert rows[0] == {
        "name": CONTEXT,
        "mode": "REQUIRED",
        "producer_kind": "cross_repo",
        "caller_workflow": "internal-sibling-pins.yml",
        "caller_job": "internal-sibling-pins",
        "cross_repo_ref": caller["uses"],
        "skip_semantics": "never",
        "rationale": rows[0]["rationale"],
    }
    assert "OMN-21478" in rows[0]["rationale"]
    assert "2026-10-16" in rows[0]["rationale"]
