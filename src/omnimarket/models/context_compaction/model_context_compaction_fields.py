# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Bounded string types shared by the compaction commands and receipts (OMN-21112).

A receipt may hold a string only as an id, a hash, an enum member or a name, so
each string type here is fenced by a pattern the JSON schema carries.
"""

from __future__ import annotations

from typing import Annotated

from omnibase_core.enums.enum_context_factor import EnumContextFactor
from pydantic import BaseModel, ConfigDict, Field

from omnimarket.enums.enum_context_compaction import EnumContextCaptureStream

SHA256_PATTERN = r"^[0-9a-f]{64}$"
SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$"

Sha256Hex = Annotated[str, Field(pattern=SHA256_PATTERN)]
SessionId = Annotated[str, Field(pattern=SESSION_ID_PATTERN)]
TranscriptRecordId = Annotated[str, Field(pattern=SESSION_ID_PATTERN)]
Name = Annotated[str, Field(pattern=NAME_PATTERN)]


class ModelContextFactorTokenEstimate(BaseModel):
    """One factor of a built package and its token estimate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    factor: EnumContextFactor
    token_estimate: int = Field(ge=0)


class ModelContextVersionVectorEntry(BaseModel):
    """One source of a candidate's version vector and the version it was read at."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Name
    version: Name


class ModelContextRouteIdentity(BaseModel):
    """The task class and model one model-written section was routed to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_class: Name
    model_name: Name


class ModelContextBarrierMark(BaseModel):
    """A session barrier's high-water on one capture stream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stream: EnumContextCaptureStream
    high_water: int = Field(ge=0)


__all__ = [
    "NAME_PATTERN",
    "SESSION_ID_PATTERN",
    "SHA256_PATTERN",
    "ModelContextBarrierMark",
    "ModelContextFactorTokenEstimate",
    "ModelContextRouteIdentity",
    "ModelContextVersionVectorEntry",
    "Name",
    "SessionId",
    "Sha256Hex",
    "TranscriptRecordId",
]
