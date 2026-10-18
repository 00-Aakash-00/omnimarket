# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Contract-owned retrieval targets (OMN-21132, TC.9, AP-E1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelRetrievalEvalTargets(BaseModel):
    """Recall threshold and reproducible percentile bootstrap settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_k: int = Field(ge=1)
    min_recall_at_k: float = Field(ge=0, le=1)
    interval_confidence: float = Field(gt=0, lt=1)
    bootstrap_resamples: int = Field(ge=100)
    bootstrap_seed: int
