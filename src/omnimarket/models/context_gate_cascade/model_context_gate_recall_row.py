# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Exact recall numerators and denominator for one case and repeat."""

from pydantic import BaseModel, ConfigDict, Field


class ModelContextGateRecallRow(BaseModel):
    """An empty label set is not comparable, rather than a fabricated recall of one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    repeat: int
    relevant_count: int = Field(ge=0)
    ungated_hits: int = Field(ge=0)
    cascade_hits: int = Field(ge=0)
    comparable: bool
    recall_equal: bool
    parse_valid: bool
    ungated_cost_microusd: int = Field(ge=0)
    cascade_cost_microusd: int = Field(ge=0)
    cost_saved_microusd: int
