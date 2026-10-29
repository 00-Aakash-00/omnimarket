# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The tool-output model consumes real captured output at the contract pin."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from omnimarket.nodes.node_model_swap_validation_effect.models.model_rightmodeler_report import (
    ModelRightmodelerReport,
)

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["report_recommend.json", "report_abstain.json"])
def test_pinned_report_accepts_captured_output(name: str) -> None:
    source = json.loads((_FIXTURES / name).read_text())
    report = ModelRightmodelerReport.model_validate(source)
    assert (
        report.families[0].verdict.decision
        == source["families"][0]["verdict"]["decision"]
    )
    assert report.spend.total_cost_usd == source["spend"]["totalCostUsd"]


def test_decision_is_required_in_captured_output() -> None:
    source = json.loads((_FIXTURES / "report_recommend.json").read_text())
    missing = copy.deepcopy(source)
    del missing["families"][0]["verdict"]["decision"]
    with pytest.raises(ValidationError):
        ModelRightmodelerReport.model_validate(missing)


@pytest.mark.parametrize("value", ["0.9", True, float("nan"), float("inf")])
def test_pinned_report_refuses_type_drift_or_nonfinite_bounds(value: object) -> None:
    source = json.loads((_FIXTURES / "report_recommend.json").read_text())
    source["families"][0]["verdict"]["worstCaseBound"] = value
    with pytest.raises(ValidationError):
        ModelRightmodelerReport.model_validate(source)
