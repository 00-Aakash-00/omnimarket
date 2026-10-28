# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Forward-only credential last-used write capability at the database boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProtocolCredentialLastUsedWrite(Protocol):
    def record_credential_last_used(self, api_key_ref: str, used_at: str) -> bool:
        """Move ``last_used_at`` forward to ``used_at`` on the key's existing row.

        One statement: a key with no row is not inserted, and a stored time
        later than ``used_at`` is not lowered. Returns whether a row changed.
        """
        ...
