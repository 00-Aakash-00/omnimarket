# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Complete retrieval evaluation report (OMN-21132, TC.9, AP-E1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from omnimarket.models.retrieval_eval.model_retrieval_eval_targets import (
    ModelRetrievalEvalTargets,
)
from omnimarket.models.retrieval_eval.model_retrieval_query_score import (
    ModelRetrievalQueryScore,
)
from omnimarket.models.retrieval_eval.model_retrieval_slice_report import (
    ModelRetrievalSliceReport,
)


class ModelRetrievalEvalReport(BaseModel):
    """Sorted cutoffs and slices, query scores in request order, and applied targets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    targets: ModelRetrievalEvalTargets
    k_values: tuple[int, ...]
    query_scores: tuple[ModelRetrievalQueryScore, ...]
    overall: ModelRetrievalSliceReport
    slices: tuple[ModelRetrievalSliceReport, ...]
