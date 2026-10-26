# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Reader port for a session's captured compact content and published activations."""

from __future__ import annotations

from typing import Protocol

from omnimarket.models.context_install_confirm import (
    ModelInstallConfirmRequest,
    ModelPublishedActivation,
)
from omnimarket.models.context_install_confirm_trigger import (
    ModelResolvedSessionCompactSnapshot,
    ModelSessionCompactSnapshot,
    ModelSessionContentProjectionAppliedEvent,
)


class ProtocolSessionCompactSource(Protocol):
    """Read compact content chunks and published activations for exactly one session."""

    def read(self, session_id: str) -> ModelSessionCompactSnapshot:
        """Return the session snapshot through an explicitly bound reader."""
        ...


class ProtocolPublishedActivationSource(Protocol):
    """Read durable published activations for exactly the requested session."""

    async def read(self, session_id: str) -> tuple[ModelPublishedActivation, ...]: ...


class ProtocolSessionCompactProjectionSource(Protocol):
    """Resolve the writer's applied event and read its session snapshot."""

    async def read_applied(
        self, event: ModelSessionContentProjectionAppliedEvent
    ) -> ModelResolvedSessionCompactSnapshot: ...


class ProtocolInstallConfirmPublisher(Protocol):
    """Publish the unwrapped request to the contract's install-confirm command."""

    async def publish(self, request: ModelInstallConfirmRequest) -> None: ...


__all__ = [
    "ProtocolInstallConfirmPublisher",
    "ProtocolPublishedActivationSource",
    "ProtocolSessionCompactProjectionSource",
    "ProtocolSessionCompactSource",
]
