# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Pinned cohort and upstream winner evidence for one profile/gate/rung pair."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from omnimarket.models.context_gate_cascade.model_context_gate_trial import (
    ModelContextGateTrial,
)


class ModelContextGateCascadeRequest(BaseModel):
    """Score recordings only; this request neither selects nor invokes a model.

    Visibility is supplied by the recording's work-unit attribution, never
    inferred from the repository hosting the scorer. No content is accepted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    role: Literal["extractor", "selector"]
    gate: Literal["kev-9b", "kev-27b", "semif", "laya", "jev"]
    rung_id: str = Field(min_length=1)
    winner_evidence_id: str = Field(min_length=1)
    winner_passed: StrictBool
    gate_training_evidence_id: str | None = Field(default=None, min_length=1)
    work_unit_repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    repository_visibility: Literal["public", "private", "unknown"]
    case_ids: tuple[str, ...] = Field(min_length=1)
    trials: tuple[ModelContextGateTrial, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_cohort(self) -> ModelContextGateCascadeRequest:
        if self.gate == "jev" and self.repository_visibility != "public":
            raise ValueError("Jev requires public work-unit repository attribution")
        if self.gate == "laya" and (
            self.gate_training_evidence_id is None
            or not self.gate_training_evidence_id.strip()
        ):
            raise ValueError("Laya requires label-training evidence attribution")
        if len(set(self.case_ids)) != len(self.case_ids) or any(
            not case.strip() for case in self.case_ids
        ):
            raise ValueError("case ids must be nonempty and unique")
        expected = {(case, repeat) for case in self.case_ids for repeat in (1, 2, 3)}
        observed = {(trial.case_id, trial.repeat) for trial in self.trials}
        if observed != expected or len(self.trials) != len(expected):
            raise ValueError(
                "exactly three distinct repeats of every declared case required"
            )
        labels: dict[str, frozenset[str]] = {}
        for trial in self.trials:
            current = frozenset(trial.relevant_ids)
            if trial.case_id in labels and labels[trial.case_id] != current:
                raise ValueError("case labels must be identical across repeats")
            labels[trial.case_id] = current
        return self
