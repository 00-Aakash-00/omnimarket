# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Typed contract configuration for review defect catalog rendering."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelReviewDefectCatalogConfig(BaseModel):
    """The contract's ``catalog`` block, shared by every resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default_token_budget: int = Field(..., ge=1)
    chars_per_token: int = Field(..., ge=1)
    packaged_catalog: str = Field(..., min_length=1)


__all__ = ["ModelReviewDefectCatalogConfig"]
