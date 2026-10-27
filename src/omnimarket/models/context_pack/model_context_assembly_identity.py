# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Caller-pinned identity of the inputs used to assemble a context pack."""

from pydantic import BaseModel, ConfigDict, Field


class ModelContextAssemblyIdentity(BaseModel):
    """Versions that must invalidate an assembly even when its text is unchanged."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus_version: str = Field(min_length=1)
    ruleset_hash: str = Field(min_length=1)
    tokenizer_id: str = Field(min_length=1)
