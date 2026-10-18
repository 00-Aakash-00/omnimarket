# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Pure TE.7 per-case and per-repeat recall/cost comparison."""

from omnimarket.models.context_gate_cascade.model_context_gate_cascade_request import (
    ModelContextGateCascadeRequest,
)
from omnimarket.models.context_gate_cascade.model_context_gate_cascade_result import (
    GateCascadeReason,
    ModelContextGateCascadeResult,
)
from omnimarket.models.context_gate_cascade.model_context_gate_recall_row import (
    ModelContextGateRecallRow,
)


class HandlerContextGateCascade:
    """Compare admitted propositions; never generate, call a gate or render content."""

    def handle(
        self, request: ModelContextGateCascadeRequest
    ) -> ModelContextGateCascadeResult:
        rows = tuple(
            ModelContextGateRecallRow(
                case_id=trial.case_id,
                repeat=trial.repeat,
                relevant_count=len(trial.relevant_ids),
                ungated_hits=len(trial.ungated_recalled_ids),
                cascade_hits=len(trial.cascade_recalled_ids),
                comparable=bool(trial.relevant_ids),
                # Preserve the admitted propositions as well as the recall count:
                # trading one missed ruling for another cannot pass this gate.
                recall_equal=set(trial.ungated_recalled_ids)
                == set(trial.cascade_recalled_ids),
                parse_valid=trial.parse_valid,
                ungated_cost_microusd=trial.ungated_cost_microusd,
                cascade_cost_microusd=trial.gate_cost_microusd
                + trial.cascade_rung_cost_microusd,
                cost_saved_microusd=trial.ungated_cost_microusd
                - trial.gate_cost_microusd
                - trial.cascade_rung_cost_microusd,
            )
            for trial in sorted(
                request.trials, key=lambda trial: (trial.case_id, trial.repeat)
            )
        )
        ungated_cost = sum(row.ungated_cost_microusd for row in rows)
        cascade_cost = sum(row.cascade_cost_microusd for row in rows)
        reasons: list[GateCascadeReason] = []
        if not request.winner_passed:
            reasons.append("upstream_winner_not_passed")
        if any(not row.recall_equal for row in rows):
            reasons.append("recall_mismatch")
        if any(not row.parse_valid for row in rows):
            reasons.append("parse_failure")
        if not any(row.ungated_hits for row in rows):
            reasons.append("no_positive_recall")
        if cascade_cost >= ungated_cost:
            reasons.append("no_cost_saved")
        return ModelContextGateCascadeResult(
            run_id=request.run_id,
            profile=request.profile,
            role=request.role,
            gate=request.gate,
            rung_id=request.rung_id,
            winner_evidence_id=request.winner_evidence_id,
            gate_training_evidence_id=request.gate_training_evidence_id,
            work_unit_repository=request.work_unit_repository,
            repository_visibility=request.repository_visibility,
            recommended=not reasons,
            reasons=tuple(reasons),
            rows=rows,
            ungated_cost_microusd=ungated_cost,
            cascade_cost_microusd=cascade_cost,
            cost_saved_microusd=ungated_cost - cascade_cost,
        )
