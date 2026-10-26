# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Contract-driven effect for install-confirm triggering (OMN-21115, TA.13).

The database/bus adapter implementing ProtocolSessionCompactSource is deferred to
a later ticket. That adapter must resolve the applied event's event_id to its
session_id, inject the reader, and publish only result.request when request_ready
to the contract's requested command topic. The session-content writer currently
reports event_id and rows_upserted, so its raw applied payload is not the reduced
ModelSessionContentAppliedEvent input. No database or bus implementation lives here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from omnibase_core.nodes.node_effect import NodeEffect

if TYPE_CHECKING:
    from omnibase_core.models.container.model_onex_container import ModelONEXContainer


class NodeContextInstallConfirmTriggerEffect(NodeEffect):
    """Effect node: read captured compact content and prepare install confirmation."""

    def __init__(self, container: ModelONEXContainer) -> None:
        super().__init__(container)


__all__ = ["NodeContextInstallConfirmTriggerEffect"]
