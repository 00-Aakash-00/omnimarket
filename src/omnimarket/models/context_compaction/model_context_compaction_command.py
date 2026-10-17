# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""The two restricted compaction commands (OMN-21112).

Both ride the ``onex.cmd.omniintelligence`` family. The request carries no
conversation content but names a session's compaction; the package reply
carries the installed messages, chunked when one record would exceed the broker
limit. Both are keyed by the adapter's ``invocation_id``.
"""

from __future__ import annotations

from typing import ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnimarket.enums.enum_context_compaction import EnumContextCompactionTrigger
from omnimarket.models.context_compaction.model_context_compaction_fields import (
    ModelContextBarrierMark,
    Name,
    SessionId,
    Sha256Hex,
    TranscriptRecordId,
)


class ModelContextCompactionRequested(BaseModel):
    """The adapter asks the runtime for a compaction package for one session."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    IDENTITY_FIELD: ClassVar[str] = "invocation_id"

    invocation_id: UUID
    session_id: SessionId
    cwd: str = Field(min_length=1, max_length=4096)
    trigger: EnumContextCompactionTrigger
    input_messages_hash: Sha256Hex
    newest_turn: TranscriptRecordId | None = None
    compact_model: Name | None = None
    session_barrier_id: UUID | None = None
    session_barrier: tuple[ModelContextBarrierMark, ...] = ()

    @model_validator(mode="after")
    def _barrier_marks_need_their_id(self) -> Self:
        if bool(self.session_barrier) != (self.session_barrier_id is not None):
            raise ValueError(
                "session_barrier and session_barrier_id are given together or not at all"
            )
        streams = [mark.stream for mark in self.session_barrier]
        if len(streams) != len(set(streams)):
            raise ValueError("session_barrier names each capture stream at most once")
        return self


class ModelContextCompactionMessage(BaseModel):
    """One installed message of a compaction package."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user", "assistant"]
    text: str


class ModelContextCompactionPackage(BaseModel):
    """One chunk of the package reply correlated to the request's invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    IDENTITY_FIELD: ClassVar[str] = "invocation_id"

    invocation_id: UUID
    session_id: SessionId
    activation_id: UUID
    installed_messages_hash: Sha256Hex
    chunk_index: int = Field(ge=0)
    chunk_count: int = Field(ge=1)
    messages: tuple[ModelContextCompactionMessage, ...]

    @model_validator(mode="after")
    def _chunk_index_in_range(self) -> Self:
        if self.chunk_index >= self.chunk_count:
            raise ValueError("chunk_index must be below chunk_count")
        return self


__all__ = [
    "ModelContextCompactionMessage",
    "ModelContextCompactionPackage",
    "ModelContextCompactionRequested",
]
