# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Contract-driven effect for install-confirm triggering (OMN-21115, TA.13).

SessionCompactProjectionSource now resolves the writer's event_id and reads the
session's compact packages in one statement. The handler's handle_projected entry
publishes only an unwrapped request through an explicit publisher port. Runtime
assembly is still pending: the durable published-activation source and bus
publisher must be bound, and the contract's reduced session-id route must be
replaced by the actual projection-applied entry. This node remains a draft.
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
