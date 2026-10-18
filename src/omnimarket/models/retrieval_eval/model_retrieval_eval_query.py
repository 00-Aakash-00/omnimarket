# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""One ranked retrieval and its relevance labels (OMN-21132, TC.9, AP-E1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnimarket.models.retrieval_eval.enum_retrieval_query_outcome import (
    EnumRetrievalQueryOutcome,
)

OVERALL_SLICE_NAME = "__all__"


class ModelRetrievalEvalQuery(BaseModel):
    """Unique document ids, ranked best first, with a nonempty relevance set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query_id: str = Field(min_length=1)
    slice_name: str = Field(min_length=1)
    outcome: EnumRetrievalQueryOutcome = EnumRetrievalQueryOutcome.OK
    retrieved_ids: tuple[str, ...] = ()
    relevant_ids: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_ids(self) -> ModelRetrievalEvalQuery:
        if not self.relevant_ids:
            raise ValueError("relevant_ids must be non-empty")
        if self.slice_name == OVERALL_SLICE_NAME:
            raise ValueError(
                f"slice_name {OVERALL_SLICE_NAME!r} is reserved for the overall row"
            )
        if len(set(self.relevant_ids)) != len(self.relevant_ids):
            raise ValueError("relevant_ids must be unique")
        if len(set(self.retrieved_ids)) != len(self.retrieved_ids):
            raise ValueError("retrieved_ids must be unique")
        if self.outcome is EnumRetrievalQueryOutcome.FAILED and self.retrieved_ids:
            raise ValueError("FAILED query must have empty retrieved_ids")
        return self
