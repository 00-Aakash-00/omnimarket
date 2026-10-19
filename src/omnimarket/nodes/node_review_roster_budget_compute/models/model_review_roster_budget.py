# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Typed inputs and projections for the soft roster budget decision."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonnegativeDecimal = Annotated[
    Decimal, Field(ge=0, allow_inf_nan=False, max_digits=24, decimal_places=12)
]
BillingMode = Literal["synchronous", "batch"]
SampleSource = Literal["live", "replay"]
RefusalReason = Literal[
    "insufficient_cost_sample",
    "model_routing_unavailable",
    "job_budget_exceeded",
    "monthly_budget_exceeded",
    "key_allowance_exceeded",
]


class ModelReviewRosterPrimaryShape(BaseModel):
    """Schema projection for the required primary declared by the caller."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    tier: Literal["required"]
    mode: Literal["inline"]
    persona_position: Literal[1]


class ModelReviewRosterBudgetedShape(BaseModel):
    """Schema projection for an additional persona declared by the caller."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    tier: Literal["budgeted"]


class ModelReviewRosterPersona(BaseModel):
    """One call position; personas remain inside their configured voter identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    voter_id: str = Field(min_length=1)
    persona_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_family: Literal["lab", "sonnet", "opus"]
    persona_position: int = Field(ge=1, le=3)
    tier: Literal["required", "budgeted"]
    mode: Literal["inline", "advisory"]

    @model_validator(mode="after")
    def validate_family(self) -> ModelReviewRosterPersona:
        if (
            self.model_family != "lab"
            and self.model_family not in self.model_id.lower()
        ):
            raise ValueError("model_family must match the served model_id")
        if self.model_family == "lab" and (
            "opus" in self.model_id.lower() or "sonnet" in self.model_id.lower()
        ):
            raise ValueError("an API model cannot be declared as a free lab voter")
        if self.model_family == "lab":
            ModelReviewRosterPrimaryShape.model_validate(self)
        return self


class ModelReviewRosterCandidate(BaseModel):
    """Candidates are ordered by increasing richness by the caller's policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    roster_id: str = Field(min_length=1)
    members: tuple[ModelReviewRosterPersona, ...] = Field(min_length=2)
    worst_case_seconds: NonnegativeDecimal = Field(
        description="Full job bound, including personas, chunks, attempts and splits."
    )

    @model_validator(mode="after")
    def validate_members(self) -> ModelReviewRosterCandidate:
        identities = [(m.voter_id, m.persona_id) for m in self.members]
        positions = [(m.voter_id, m.persona_position) for m in self.members]
        if len(set(identities)) != len(identities) or len(set(positions)) != len(
            positions
        ):
            raise ValueError("duplicate voter persona or persona position")
        for voter_id in {m.voter_id for m in self.members}:
            members = [m for m in self.members if m.voter_id == voter_id]
            if len({(m.model_id, m.model_family) for m in members}) != 1:
                raise ValueError("one voter must serve one model")
            if sorted(m.persona_position for m in members) != list(
                range(1, len(members) + 1)
            ):
                raise ValueError("persona positions must be contiguous from one")
        return self


class ModelReviewRosterCostSample(BaseModel):
    """A measured mean for one model, persona position and billing mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1)
    persona_position: int = Field(ge=1, le=3)
    billing_mode: BillingMode
    source: SampleSource
    sample_size: int = Field(ge=0)
    mean_cost_usd: NonnegativeDecimal


class ModelReviewRosterBudgetRequest(BaseModel):
    """Explicit cycle state: no clock, store access, price inference or I/O."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_rosters: tuple[ModelReviewRosterCandidate, ...] = Field(min_length=1)
    cost_samples: tuple[ModelReviewRosterCostSample, ...]
    reviews_per_day: NonnegativeDecimal
    days_remaining: NonnegativeDecimal
    cycle_spend_usd: NonnegativeDecimal
    key_remaining_allowances_usd: tuple[NonnegativeDecimal, ...] = Field(
        description="Spendable amounts after reserves and outstanding reservations."
    )
    monthly_budget_usd: NonnegativeDecimal = Field(default=Decimal("800"), gt=0)
    safety_factor: NonnegativeDecimal = Field(default=Decimal("0.85"), gt=0, le=1)
    job_budget_seconds: NonnegativeDecimal = Field(
        default=Decimal("3600"), gt=0, le=3600
    )
    opus_model_routing_ready: bool = False
    opus_per_model_pricing_ready: bool = False

    @model_validator(mode="after")
    def validate_policy(self) -> ModelReviewRosterBudgetRequest:
        ids = [r.roster_id for r in self.candidate_rosters]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate roster_id")
        floor = self.candidate_rosters[0]
        if floor.roster_id != "R0" or len(floor.members) != 2:
            raise ValueError(
                "R0 must be first and contain exactly the lab and Sonnet floor"
            )
        if {m.model_family for m in floor.members} != {"lab", "sonnet"}:
            raise ValueError("R0 requires one lab and one Sonnet inline primary")
        for member in floor.members:
            ModelReviewRosterPrimaryShape.model_validate(member)
        for roster in self.candidate_rosters[1:]:
            if any(m not in roster.members for m in floor.members):
                raise ValueError("each candidate must retain the R0 floor")
            for member in roster.members:
                if member not in floor.members:
                    ModelReviewRosterBudgetedShape.model_validate(member)
        keys = [
            (s.model_id, s.persona_position, s.billing_mode, s.source)
            for s in self.cost_samples
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate cost sample")
        return self


class ModelReviewRosterCostEstimate(ModelReviewRosterCostSample):
    """The actual sample used for this persona, with provenance in the result."""

    voter_id: str
    persona_id: str


class ModelReviewRosterProjection(BaseModel):
    """Every candidate is reported, including refusals and missing estimates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    roster_id: str
    projected_remaining_cost_usd: Decimal | None = None
    projected_cycle_cost_usd: Decimal | None = None
    worst_case_seconds: Decimal
    estimates: tuple[ModelReviewRosterCostEstimate, ...]
    refusal_reasons: tuple[RefusalReason, ...]


class ModelReviewRosterBudgetResult(BaseModel):
    """A soft policy decision; no overlay write or hard admission is performed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: Literal["selected", "no_affordable_roster"]
    selected_roster_id: str | None = None
    projected_reviews: Decimal
    working_budget_usd: Decimal
    remaining_key_allowance_usd: Decimal
    projections: tuple[ModelReviewRosterProjection, ...]
