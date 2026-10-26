# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Reader port for a session's captured compact content and published activations."""

from __future__ import annotations

from typing import Protocol

from omnimarket.models.context_install_confirm_trigger import (
    ModelSessionCompactSnapshot,
)


class ProtocolSessionCompactSource(Protocol):
    """Read compact content chunks and published activations for exactly one session."""

    def read(self, session_id: str) -> ModelSessionCompactSnapshot:
        """Return the session snapshot; database/bus implementation is a later ticket."""
        ...


__all__ = ["ProtocolSessionCompactSource"]
