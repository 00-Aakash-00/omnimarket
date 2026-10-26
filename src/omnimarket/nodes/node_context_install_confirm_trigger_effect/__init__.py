# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Trigger install confirmation from projected session content (OMN-21115, TA.13).

The ProtocolSessionCompactSource database/bus adapter is deferred to a later ticket;
see node.py for the session identity and conditional publication responsibilities.
"""

from omnimarket.nodes.node_context_install_confirm_trigger_effect.handlers.handler_context_install_confirm_trigger import (
    HandlerContextInstallConfirmTrigger,
)

__all__ = ["HandlerContextInstallConfirmTrigger"]
