# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Wire types for applying the release gate rulesets (OMN-21433)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelReleaseGateRulesetsRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = Field(min_length=3)
    directory: str = ".github/rulesets"
    train_app_id: str = ""
    apply: bool = False


class ModelReleaseGateRulesetsResult(BaseModel):
    """One line per declaration: PLAN or APPLY, then create or update, then the name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lines: tuple[str, ...]
