# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Canonical node that collects the facts the release gate judges."""

from omnimarket.nodes.node_release_gate_collect_effect.handlers.handler_release_gate_collect import (
    HandlerReleaseGateCollect,
)


class NodeReleaseGateCollectEffect(HandlerReleaseGateCollect):
    """Declarative entry point; the collection logic lives in the handler."""


__all__ = ["NodeReleaseGateCollectEffect"]
