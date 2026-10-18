# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Per-query retrieval metrics (OMN-21132, TC.9, AP-E1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnimarket.models.retrieval_eval.enum_retrieval_query_outcome import (
    EnumRetrievalQueryOutcome,
)


class ModelRetrievalQueryScore(BaseModel):
    """Recall, precision, AP and reciprocal rank, including failed queries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query_id: str
    slice_name: str
    outcome: EnumRetrievalQueryOutcome
    recall_at_k: dict[int, float]
    precision_at_k: dict[int, float]
    average_precision: float
    reciprocal_rank: float
