# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Slice and overall retrieval aggregates (OMN-21132, TC.9, AP-E1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnimarket.models.retrieval_eval.model_retrieval_metric_interval import (
    ModelRetrievalMetricInterval,
)


class ModelRetrievalSliceReport(BaseModel):
    """Arithmetic means over every query; __all__ names the overall row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slice_name: str
    query_count: int
    failed_query_count: int
    mean_recall_at_k: dict[int, float]
    mean_precision_at_k: dict[int, float]
    mean_average_precision: float
    mean_reciprocal_rank: float
    recall_at_target_k_interval: ModelRetrievalMetricInterval
    meets_target: bool
