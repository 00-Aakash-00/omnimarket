# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Wire type for collecting the release gate's facts (OMN-21433)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelReleaseGateCollectRequest(BaseModel):
    """Where the GitHub event and the committed policy are; the overlay path is optional."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = Field(min_length=3)
    event_name: str = Field(min_length=1)
    event_path: str = Field(min_length=1)
    policy_path: str = Field(min_length=1)
    identities_path: str = ""
