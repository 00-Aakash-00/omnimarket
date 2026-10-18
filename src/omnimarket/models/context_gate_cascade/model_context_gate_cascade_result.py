# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""A measured verdict, with no text or package output channel."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from omnimarket.models.context_gate_cascade.model_context_gate_recall_row import (
    ModelContextGateRecallRow,
)

GateCascadeReason = Literal[
    "upstream_winner_not_passed",
    "recall_mismatch",
    "parse_failure",
    "no_positive_recall",
    "no_cost_saved",
]


class ModelContextGateCascadeResult(BaseModel):
    """Recommendation is scoped to this supplied cohort, profile, gate and winner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    profile: str
    role: Literal["extractor", "selector"]
    gate: Literal["kev-9b", "kev-27b", "semif", "laya", "jev"]
    rung_id: str
    winner_evidence_id: str
    gate_training_evidence_id: str | None
    work_unit_repository: str
    repository_visibility: Literal["public", "private", "unknown"]
    recommended: bool
    reasons: tuple[GateCascadeReason, ...]
    rows: tuple[ModelContextGateRecallRow, ...]
    ungated_cost_microusd: int
    cascade_cost_microusd: int
    cost_saved_microusd: int
