# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Read the compact packages selected by a committed session-content event."""

from __future__ import annotations

from typing import Protocol

from omnimarket.models.context_install_confirm_request import ModelCapturedCompactChunk
from omnimarket.models.context_install_confirm_trigger import (
    ModelResolvedSessionCompactSnapshot,
    ModelSessionCompactSnapshot,
    ModelSessionContentProjectionAppliedEvent,
)
from omnimarket.protocols.protocol_session_compact_source import (
    ProtocolPublishedActivationSource,
)


class ProtocolSessionContentReadDatabase(Protocol):
    """The runtime's parameterized database read boundary; no driver or DSN here."""

    async def execute(self, query: str, *params: object) -> list[dict[str, object]]: ...


# Resolve identity, compact chunks, and the newest content timestamp in ONE
# statement snapshot. A later ordinary turn must participate in capture-gap
# detection even though its content is never passed to the boundary parser.
_READ_APPLIED = """
    WITH applied AS (
        SELECT session_id FROM omninode_internal.session_content WHERE event_id = $1
    ), newest AS (
        SELECT MAX(c.emitted_at) AS newest_captured_at
        FROM omninode_internal.session_content c
        JOIN applied a ON c.session_id = a.session_id
    )
    SELECT a.session_id, n.newest_captured_at,
           c.event_id, c.chunk_index, c.chunk_count, c.content,
           c.content_sha256, c.truncated, c.emitted_at
    FROM applied a CROSS JOIN newest n
    LEFT JOIN omninode_internal.session_content c
      ON c.session_id = a.session_id AND c.content_kind = 'compact'
    ORDER BY c.emitted_at, c.event_id
"""


class SessionCompactProjectionSource:
    """Read session content and join an explicitly injected activation source.

    The caller owns connection lifetime and the activation source's durable
    implementation. Failure of either source propagates; it is never converted
    to a plausible empty activation list.
    """

    def __init__(
        self,
        database: ProtocolSessionContentReadDatabase,
        activations: ProtocolPublishedActivationSource,
    ) -> None:
        self._database = database
        self._activations = activations

    async def read_applied(
        self, event: ModelSessionContentProjectionAppliedEvent
    ) -> ModelResolvedSessionCompactSnapshot:
        rows = await self._database.execute(_READ_APPLIED, event.event_id)
        if not rows:
            raise ValueError("applied event is not projected in session_content")
        session_id = rows[0]["session_id"]
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("applied event has no projected session identity")
        newest = rows[0]["newest_captured_at"]
        chunks: list[ModelCapturedCompactChunk] = []
        for row in rows:
            if row["session_id"] != session_id or row["newest_captured_at"] != newest:
                raise ValueError("inconsistent session-content projection snapshot")
            if row["event_id"] is not None:
                chunks.append(
                    ModelCapturedCompactChunk.model_validate(
                        {
                            key: value
                            for key, value in row.items()
                            if key != "newest_captured_at"
                        }
                    )
                )
        snapshot = ModelSessionCompactSnapshot.model_validate(
            {
                "chunks": tuple(chunks),
                "activations": await self._activations.read(session_id),
                "newest_captured_at": newest,
            }
        )
        return ModelResolvedSessionCompactSnapshot(
            session_id=session_id, snapshot=snapshot
        )


__all__ = ["SessionCompactProjectionSource"]
