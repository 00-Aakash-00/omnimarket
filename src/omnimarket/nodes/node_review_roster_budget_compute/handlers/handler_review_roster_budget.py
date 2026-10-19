# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Pure roster selection from measured costs and caller-supplied cycle state."""

from __future__ import annotations

from decimal import Decimal, localcontext

from omnimarket.nodes.node_review_roster_budget_compute.models.model_review_roster_budget import (
    BillingMode,
    ModelReviewRosterBudgetRequest,
    ModelReviewRosterBudgetResult,
    ModelReviewRosterCostEstimate,
    ModelReviewRosterCostSample,
    ModelReviewRosterPersona,
    ModelReviewRosterProjection,
    RefusalReason,
)


def _sample_for(
    request: ModelReviewRosterBudgetRequest, member: ModelReviewRosterPersona
) -> ModelReviewRosterCostSample | None:
    mode: BillingMode = "synchronous" if member.mode == "inline" else "batch"
    samples = {
        s.source: s
        for s in request.cost_samples
        if s.model_id == member.model_id
        and s.persona_position == member.persona_position
        and s.billing_mode == mode
    }
    live = samples.get("live")
    if live is not None and live.sample_size >= (200 if mode == "synchronous" else 1):
        return live
    replay = samples.get("replay")
    return replay if replay is not None and replay.sample_size > 0 else None


class HandlerReviewRosterBudget:
    """Choose the last affordable candidate in the configured richness order."""

    def handle(
        self, request: ModelReviewRosterBudgetRequest
    ) -> ModelReviewRosterBudgetResult:
        # Inputs have at most 24 digits. Three products can need up to 72;
        # keep eligibility exact even if the process's Decimal context differs.
        with localcontext() as context:
            context.prec = 80
            return self._decide(request)

    def _decide(
        self, request: ModelReviewRosterBudgetRequest
    ) -> ModelReviewRosterBudgetResult:
        volume = request.reviews_per_day * request.days_remaining
        ceiling = request.monthly_budget_usd * request.safety_factor
        allowance = sum(request.key_remaining_allowances_usd, Decimal(0))
        projections: list[ModelReviewRosterProjection] = []
        for roster in request.candidate_rosters:
            reasons: list[RefusalReason] = []
            estimates: list[ModelReviewRosterCostEstimate] = []
            missing_cost = False
            for member in roster.members:
                if member.model_family == "lab":
                    continue
                if (
                    member.model_family == "opus" or "opus" in member.model_id.lower()
                ) and not (
                    request.opus_model_routing_ready
                    and request.opus_per_model_pricing_ready
                ):
                    reasons.append("model_routing_unavailable")
                sample = _sample_for(request, member)
                if sample is None:
                    missing_cost = True
                    reasons.append("insufficient_cost_sample")
                else:
                    estimates.append(
                        ModelReviewRosterCostEstimate(
                            **sample.model_dump(),
                            voter_id=member.voter_id,
                            persona_id=member.persona_id,
                        )
                    )
            remaining = (
                None
                if missing_cost
                else volume * sum((e.mean_cost_usd for e in estimates), Decimal(0))
            )
            total = None if remaining is None else request.cycle_spend_usd + remaining
            if roster.worst_case_seconds > request.job_budget_seconds:
                reasons.append("job_budget_exceeded")
            if total is not None and total > ceiling:
                reasons.append("monthly_budget_exceeded")
            if remaining is not None and remaining > allowance:
                reasons.append("key_allowance_exceeded")
            projections.append(
                ModelReviewRosterProjection(
                    roster_id=roster.roster_id,
                    projected_remaining_cost_usd=remaining,
                    projected_cycle_cost_usd=total,
                    worst_case_seconds=roster.worst_case_seconds,
                    estimates=tuple(estimates),
                    refusal_reasons=tuple(dict.fromkeys(reasons)),
                )
            )
        selected = None
        if not projections[0].refusal_reasons:
            for projection in projections:
                if not projection.refusal_reasons:
                    selected = projection.roster_id
        return ModelReviewRosterBudgetResult(
            reason="selected" if selected is not None else "no_affordable_roster",
            selected_roster_id=selected,
            projected_reviews=volume,
            working_budget_usd=ceiling,
            remaining_key_allowance_usd=allowance,
            projections=tuple(projections),
        )
