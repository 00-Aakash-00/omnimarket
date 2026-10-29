# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Validate the qualified report interface and preserve independent evidence."""

from __future__ import annotations

from typing import Any

from omnibase_core.models.model_swap_validation.model_model_swap_validation_gate import (
    ModelModelSwapValidationGate,
)
from omnibase_core.models.model_swap_validation.model_model_swap_validation_request import (
    ModelModelSwapValidationRequest,
)
from pydantic import ValidationError

from omnimarket.nodes.node_model_swap_validation_effect.models.model_rightmodeler_report import (
    ModelRightmodelerReport,
)

from .gateway import GatewaySession

_GATES = {
    "zero-unsafe-substitutions",
    "quality",
    "evidence-coverage",
    "required-abstention",
    "availability",
}


class ReportError(ValueError):
    """A report is malformed or contradicts the requested validation."""


def parse_report(
    report: object,
    request: ModelModelSwapValidationRequest,
    gateway: GatewaySession,
) -> dict[str, Any]:
    """Return terminal evidence fields; process exits are handled by the caller.

    No family score is projected onto cases. Both ordered case verdicts and
    cost provenance come from actual gateway observations. A gated nominal
    recommendation is normalized to inconclusive with its raw decision and
    effectiveness retained explicitly.
    """
    try:
        parsed = ModelRightmodelerReport.model_validate(report)
    except ValidationError as exc:
        raise ReportError("tool report differs from the qualified schema") from exc
    if len(parsed.families) != 1 or len(parsed.verdicts) != 1 or parsed.apply:
        raise ReportError("report must contain one evidence-only task family")
    family = parsed.families[0]
    verdict = family.verdict
    if (
        family.family_id != request.task_type
        or verdict.family_id != request.task_type
        or parsed.verdicts[0].model_dump() != verdict.model_dump()
        or len(verdict.case_ids) != len(set(verdict.case_ids))
        or {gate.id for gate in family.gates} != _GATES
        or len(family.gates) != len(_GATES)
    ):
        raise ReportError("report identity or gate evidence does not match the request")
    cases = gateway.per_case
    case_ids = {case["case_id"] for case in gateway.replay_set.cases}
    if any(case.case_id not in case_ids for case in cases) or gateway.schema_mismatch:
        raise ReportError("case evidence is outside the verified replay set")
    decision = verdict.decision
    recommended = None
    if family.effective_recommendation:
        if (
            decision != "recommend"
            or verdict.candidate_id != request.candidate_model
            or verdict.worst_case_bound < request.quality_floor
            or not all(gate.passed for gate in family.gates)
            or family.selection.get("status") != "selected"
            or family.selection.get("confirmedCandidateId") != request.candidate_model
            or family.confirmation.get("status") not in {"not_required", "confirmed"}
            or not cases
            or len(cases) < len(verdict.case_ids)
        ):
            raise ReportError(
                "recommendation lacks matching independently observed evidence"
            )
        recommended = request.candidate_model
    elif decision == "recommend":
        decision = "inconclusive"
    calls = gateway.calls
    return {
        "decision": decision,
        "reported_decision": verdict.decision,
        "recommendation_effective": family.effective_recommendation,
        "recommended_model": recommended,
        "lower_bound": verdict.worst_case_bound,
        "gates": tuple(
            ModelModelSwapValidationGate.model_validate(gate.model_dump(by_alias=True))
            for gate in family.gates
        ),
        "per_case": cases,
        "judge_model": gateway.judge_model,
        "gateway_calls": calls,
        "provider_reported_cost_usd": sum(
            call.cost_usd or 0
            for call in calls
            if call.cost_measurement_source == "provider_reported_charge"
        ),
        "estimated_cost_usd": sum(
            call.cost_usd or 0
            for call in calls
            if call.cost_measurement_source == "estimated_list_price"
        ),
        "tool_estimated_cost_usd": parsed.spend.total_cost_usd,
    }


__all__ = ["ReportError", "parse_report"]
