# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Consumed fields of the CLI report captured at the contract's exact tool pin.

The retired recommendation-report schema is a different format. This model
validates the actual report command's family verdicts and spend record. Other
nested evidence is retained verbatim; it never substitutes for required fields.
A tool-pin update requires recapturing the report and rerunning these fixtures.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelRightmodelerVerdict(BaseModel):
    """The candidate, decision and quality bound as reported by the tool."""

    model_config = ConfigDict(
        extra="allow", allow_inf_nan=False, populate_by_name=True, strict=True
    )

    family_id: str = Field(min_length=1, alias="familyId")
    candidate_id: str = Field(min_length=1, alias="candidateId")
    decision: Literal["recommend", "reject", "abstain", "inconclusive"]
    worst_case_bound: float = Field(ge=0, le=1, alias="worstCaseBound")
    case_ids: list[str] = Field(alias="caseIds")


class ModelRightmodelerGate(BaseModel):
    """One unchanged tool gate, including its stated reason."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)

    id: str = Field(min_length=1)
    passed: bool = Field(alias="pass", strict=True)
    reason: str


class ModelRightmodelerFamily(BaseModel):
    """One family recommendation with its selection and gate evidence."""

    model_config = ConfigDict(
        extra="allow", allow_inf_nan=False, populate_by_name=True, strict=True
    )

    family_id: str = Field(min_length=1, alias="familyId")
    verdict: ModelRightmodelerVerdict
    selection: dict[str, Any]
    gates: list[ModelRightmodelerGate]
    decision_display: str = Field(alias="decisionDisplay")
    effective_recommendation: bool = Field(strict=True, alias="effectiveRecommendation")
    confirmation: dict[str, Any]
    reference_ceiling: dict[str, Any] = Field(alias="referenceCeiling")


class ModelRightmodelerSpend(BaseModel):
    """Tool spend is an estimate regardless of any gateway provider charges."""

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, populate_by_name=True, strict=True
    )

    events: int = Field(ge=0, strict=True)
    total_cost_usd: float = Field(ge=0, alias="totalCostUsd")
    by_actor: dict[str, dict[str, Any]] = Field(alias="byActor")


class ModelRightmodelerReport(BaseModel):
    """The pinned report command's actual JSON envelope, without fabricated fields."""

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, populate_by_name=True, strict=True
    )

    verdicts: list[ModelRightmodelerVerdict]
    families: list[ModelRightmodelerFamily]
    reference_ceilings: list[dict[str, Any]] = Field(alias="referenceCeilings")
    judge_disagreement: dict[str, Any] = Field(alias="judgeDisagreement")
    spend: ModelRightmodelerSpend
    stratum_weights: dict[str, Any] = Field(alias="stratumWeights")
    caps: list[dict[str, Any]]
    candidate_errors: list[dict[str, Any]] = Field(alias="candidateErrors")
    receipts: list[dict[str, Any]]
    blocked_families: list[dict[str, Any]] = Field(alias="blockedFamilies")
    apply: list[dict[str, Any]]
    report_path: str | None = Field(default=None, alias="reportPath")
