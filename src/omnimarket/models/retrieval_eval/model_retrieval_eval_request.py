# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Retrieval evaluation inputs and policy targets (OMN-21132, TC.9, AP-E1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnimarket.models.retrieval_eval.model_retrieval_eval_query import (
    ModelRetrievalEvalQuery,
)
from omnimarket.models.retrieval_eval.model_retrieval_eval_targets import (
    ModelRetrievalEvalTargets,
)


class ModelRetrievalEvalRequest(BaseModel):
    """Queries in caller order, unique positive cutoffs, and required targets.

    The caller fills targets from the node contract's config block with
    targets_from_contract.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    queries: tuple[ModelRetrievalEvalQuery, ...] = Field(min_length=1)
    k_values: tuple[int, ...] = Field(min_length=1)
    targets: ModelRetrievalEvalTargets

    @model_validator(mode="after")
    def _validate_queries_and_cutoffs(self) -> ModelRetrievalEvalRequest:
        ids = [query.query_id for query in self.queries]
        if len(set(ids)) != len(ids):
            raise ValueError("query_id must be unique across queries")
        if any(k < 1 for k in self.k_values):
            raise ValueError("k_values must each be >= 1")
        if len(set(self.k_values)) != len(self.k_values):
            raise ValueError("k_values must be unique")
        return self
