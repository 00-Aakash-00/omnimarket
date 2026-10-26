# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Install-confirm trigger input, reader snapshot and outcome (OMN-21115, TA.13)."""

from __future__ import annotations

from enum import StrEnum, unique

from pydantic import BaseModel, ConfigDict, Field

from omnimarket.models.context_install_confirm import (
    ModelInstallConfirmRequest,
    ModelPublishedActivation,
)
from omnimarket.models.context_install_confirm_request import ModelCapturedCompactChunk


@unique
class EnumInstallConfirmTriggerOutcome(StrEnum):
    """Whether the applied session content produced a publishable request."""

    NO_COMPACT_RECORDS = "no_compact_records"
    REQUEST_READY = "request_ready"
    ALL_PACKAGES_UNREADABLE = "all_packages_unreadable"


class ModelSessionContentAppliedEvent(BaseModel):
    """Session identity resolved from a successful session-content applied event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str = Field(min_length=1)


class ModelSessionCompactSnapshot(BaseModel):
    """Captured compact chunks and published activations read for one session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunks: tuple[ModelCapturedCompactChunk, ...] = ()
    activations: tuple[ModelPublishedActivation, ...] = ()


class ModelInstallConfirmTriggerResult(BaseModel):
    """Only ``request_ready`` carries a request for the requested command topic."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: EnumInstallConfirmTriggerOutcome
    request: ModelInstallConfirmRequest | None = None
    packages_unreadable: tuple[str, ...] = ()


__all__ = [
    "EnumInstallConfirmTriggerOutcome",
    "ModelInstallConfirmTriggerResult",
    "ModelSessionCompactSnapshot",
    "ModelSessionContentAppliedEvent",
]
