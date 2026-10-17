# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""The six compaction receipt events (OMN-21112).

Each receipt is one concept on its own ``onex.evt.omnimarket`` topic and carries
no conversation content: ids, hashes, counts, factor names, token estimates and
failure classes only. ``IDENTITY_FIELD`` names the contracted identity the
receipts projection folds on: ``candidate_id`` for ``prepared``,
``invocation_id`` for ``fallback`` and ``activation_id`` for the activation and
installation facts. Every receipt also carries ``session_id``, the partition key.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from omnimarket.enums.enum_context_compaction import (
    EnumContextCompactionFailureClass,
    EnumContextCompactionTrigger,
    EnumContextInstallUncertainReason,
)
from omnimarket.models.context_compaction.model_context_compaction_fields import (
    ModelContextFactorTokenEstimate,
    ModelContextRouteIdentity,
    ModelContextVersionVectorEntry,
    Name,
    SessionId,
    Sha256Hex,
    TranscriptRecordId,
)


class ModelContextCompactionPrepared(BaseModel):
    """A candidate package was built; identical copies fold to one candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    IDENTITY_FIELD: ClassVar[str] = "candidate_id"

    candidate_id: UUID
    session_id: SessionId
    input_messages_hash: Sha256Hex
    version_vector: tuple[ModelContextVersionVectorEntry, ...]
    version_vector_hash: Sha256Hex
    assembly_hash: Sha256Hex
    factor_token_estimates: tuple[ModelContextFactorTokenEstimate, ...]
    profile_name: Name
    profile_content_hash: Sha256Hex
    implementation_identity_hash: Sha256Hex


class ModelContextCompactionActivated(BaseModel):
    """One invocation offered one candidate; the package reply follows it."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    IDENTITY_FIELD: ClassVar[str] = "activation_id"

    activation_id: UUID
    session_id: SessionId
    candidate_id: UUID
    invocation_id: UUID
    trigger: EnumContextCompactionTrigger
    model_outputs_hash: Sha256Hex | None = None
    route_identities: tuple[ModelContextRouteIdentity, ...] = ()
    final_pack_hash: Sha256Hex
    body_hash: Sha256Hex
    installed_messages_hash: Sha256Hex


class ModelContextCompactionFallback(BaseModel):
    """An invocation failed behind the bus before activation and replied native."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    IDENTITY_FIELD: ClassVar[str] = "invocation_id"

    invocation_id: UUID
    session_id: SessionId
    candidate_id: UUID | None = None
    failure_class: EnumContextCompactionFailureClass


class ModelContextCompactionInstalled(BaseModel):
    """A captured boundary installed the activation's messages byte for byte."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    IDENTITY_FIELD: ClassVar[str] = "activation_id"

    activation_id: UUID
    session_id: SessionId
    boundary_uuid: TranscriptRecordId
    observed_hash: Sha256Hex


class ModelContextCompactionInstallMismatch(BaseModel):
    """A captured boundary names the activation but its messages hash differs."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    IDENTITY_FIELD: ClassVar[str] = "activation_id"

    activation_id: UUID
    session_id: SessionId
    boundary_uuid: TranscriptRecordId
    installed_messages_hash: Sha256Hex
    observed_hash: Sha256Hex


class ModelContextCompactionInstallUncertain(BaseModel):
    """An activation reached no installation fact, with the typed reason.

    ``boundary_uuid`` names the boundary the reason was decided at, when one was
    captured; a capture gap has none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    IDENTITY_FIELD: ClassVar[str] = "activation_id"

    activation_id: UUID
    session_id: SessionId
    reason: EnumContextInstallUncertainReason
    boundary_uuid: TranscriptRecordId | None = None


__all__ = [
    "ModelContextCompactionActivated",
    "ModelContextCompactionFallback",
    "ModelContextCompactionInstallMismatch",
    "ModelContextCompactionInstallUncertain",
    "ModelContextCompactionInstalled",
    "ModelContextCompactionPrepared",
]
