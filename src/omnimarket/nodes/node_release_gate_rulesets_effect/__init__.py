# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Canonical node that applies the release gate rulesets declared as code."""

from omnimarket.nodes.node_release_gate_rulesets_effect.handlers.handler_release_gate_rulesets import (
    HandlerReleaseGateRulesets,
)


class NodeReleaseGateRulesetsEffect(HandlerReleaseGateRulesets):
    """Declarative entry point; the plan and apply logic lives in the handler."""


__all__ = ["NodeReleaseGateRulesetsEffect"]
