# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""A paired ungated/cascade observation; proposition admission occurs upstream."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class ModelContextGateTrial(BaseModel):
    """Only admitted label ids and measured integer costs, never model prose."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    repeat: int = Field(ge=1, le=3, strict=True)
    relevant_ids: tuple[str, ...]
    ungated_recalled_ids: tuple[str, ...]
    cascade_recalled_ids: tuple[str, ...]
    gate_accepts: StrictBool
    parse_valid: StrictBool
    ungated_cost_microusd: int = Field(ge=0, strict=True)
    gate_cost_microusd: int = Field(ge=0, strict=True)
    cascade_rung_cost_microusd: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def _validate_observation(self) -> ModelContextGateTrial:
        for ids in (
            self.relevant_ids,
            self.ungated_recalled_ids,
            self.cascade_recalled_ids,
        ):
            if len(ids) != len(set(ids)) or any(not item.strip() for item in ids):
                raise ValueError("label ids must be nonempty and unique")
        relevant = set(self.relevant_ids)
        if (
            not set(self.ungated_recalled_ids) <= relevant
            or not set(self.cascade_recalled_ids) <= relevant
        ):
            raise ValueError("recalled ids must be admitted relevant label ids")
        if not self.gate_accepts and (
            self.cascade_recalled_ids or self.cascade_rung_cost_microusd
        ):
            raise ValueError("a rejected segment cannot have rung results or rung cost")
        return self
