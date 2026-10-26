# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Canonical node for release train and human grant decisions."""

from omnimarket.handlers.handler_release_gate import HandlerReleaseGate


class NodeReleaseGateCompute(HandlerReleaseGate):
    """Declarative entry point; all policy lives in the shared pure handler."""


__all__ = ["NodeReleaseGateCompute"]
