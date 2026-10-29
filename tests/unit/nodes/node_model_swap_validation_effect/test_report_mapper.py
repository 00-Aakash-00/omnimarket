# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Map actual pinned CLI reports without inventing ordered case evidence."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from omnibase_core.models.model_swap_validation.model_model_swap_validation_case import (
    ModelModelSwapValidationCase,
)
from omnibase_core.models.model_swap_validation.model_model_swap_validation_request import (
    ModelModelSwapValidationRequest,
)

from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.gateway import (
    GatewaySession,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.report import (
    ReportError,
    parse_report,
)


def _inputs() -> tuple[dict[str, Any], ModelModelSwapValidationRequest, GatewaySession]:
    root = Path(__file__).resolve().parents[4]
    capture = root / "docs/integrations/rightmodeler/captures/native-64-priced"
    report = json.loads((capture / "report.stdout.json").read_text())
    evidence = json.loads((capture / "per-case-evidence.json").read_text())
    cases = tuple(
        ModelModelSwapValidationCase.model_validate(
            {
                key: item[key]
                for key in (
                    "case_id",
                    "verdict_reference_first",
                    "verdict_candidate_first",
                    "score",
                )
            }
        )
        for item in evidence
    )
    request = ModelModelSwapValidationRequest.model_validate(
        {
            "correlation_id": uuid4(),
            "task_type": "model_swap_validation",
            "reference_model": "example-reference/large",
            "candidate_model": "example-candidate/small",
            "judge_mode": "stub",
            "answer_source": "recorded",
            "replay_set_digest": "a" * 64,
            "quality_floor": 0.85,
            "max_cost_usd": 1,
            "work_unit_repository": "synthetic/repo",
        }
    )
    gateway = cast(
        GatewaySession,
        SimpleNamespace(
            per_case=cases,
            calls=(),
            schema_mismatch=False,
            judge_model="example-judge/neutral",
            replay_set=SimpleNamespace(
                cases=tuple({"case_id": case.case_id} for case in cases)
            ),
        ),
    )
    return report, request, gateway


def test_actual_recommend_capture_keeps_lower_bound_gates_and_case_verdicts() -> None:
    report, request, gateway = _inputs()
    result = parse_report(report, request, gateway)
    assert result["decision"] == "recommend"
    assert result["reported_decision"] == "recommend"
    assert result["recommendation_effective"] is True
    assert result["recommended_model"] == request.candidate_model
    assert result["lower_bound"] == report["families"][0]["verdict"]["worstCaseBound"]
    assert [gate.model_dump(by_alias=True) for gate in result["gates"]] == report[
        "families"
    ][0]["gates"]
    assert len(result["per_case"]) == 64
    assert all(case.score == 1 for case in result["per_case"])
    assert result["tool_estimated_cost_usd"] == report["spend"]["totalCostUsd"]
    assert result["provider_reported_cost_usd"] == 0


def test_nominal_gated_recommend_retains_raw_decision_and_normalizes_inconclusive() -> (
    None
):
    report, request, gateway = _inputs()
    report["families"][0]["effectiveRecommendation"] = False
    report["families"][0]["decisionDisplay"] = "recommend (gated)"
    result = parse_report(report, request, gateway)
    assert result["decision"] == "inconclusive"
    assert result["reported_decision"] == "recommend"
    assert result["recommendation_effective"] is False
    assert result["recommended_model"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "family",
        "candidate",
        "bound",
        "gate",
        "missing",
        "top_verdict",
        "no_cases",
        "applied",
    ],
)
def test_report_contradictions_fail_closed(mutation: str) -> None:
    report, request, gateway = _inputs()
    if mutation == "family":
        report["families"][0]["familyId"] = "other_task"
    elif mutation == "candidate":
        report["families"][0]["verdict"]["candidateId"] = "other/model"
    elif mutation == "bound":
        report["families"][0]["verdict"]["worstCaseBound"] = 0.1
        report["verdicts"][0]["worstCaseBound"] = 0.1
    elif mutation == "gate":
        report["families"][0]["gates"][0]["pass"] = False
    elif mutation == "missing":
        del report["spend"]
    elif mutation == "top_verdict":
        report["verdicts"][0]["decision"] = "reject"
    elif mutation == "no_cases":
        gateway = cast(
            GatewaySession, SimpleNamespace(**{**vars(gateway), "per_case": ()})
        )
    else:
        report["apply"] = [{"state": "applied"}]
    with pytest.raises(ReportError):
        parse_report(report, request, gateway)


def test_abstention_does_not_invent_cases_from_family_score() -> None:
    report, request, gateway = _inputs()
    root = Path(__file__).resolve().parents[4]
    report = json.loads(
        (
            root
            / "docs/integrations/rightmodeler/captures/native-priced/report.stdout.json"
        ).read_text()
    )
    gateway = cast(GatewaySession, SimpleNamespace(**{**vars(gateway), "per_case": ()}))
    result = parse_report(report, request, gateway)
    assert result["decision"] == "abstain"
    assert result["per_case"] == ()
    assert result["recommended_model"] is None
