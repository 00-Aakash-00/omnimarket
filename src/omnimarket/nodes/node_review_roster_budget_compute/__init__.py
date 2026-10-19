# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Review roster budget compute: deterministic, measured, without side effects."""

from omnimarket.nodes.node_review_roster_budget_compute.handlers.handler_review_roster_budget import (
    HandlerReviewRosterBudget,
)


class NodeReviewRosterBudgetCompute(HandlerReviewRosterBudget):
    """Declarative ONEX entry point for the pure roster handler."""


__all__ = ["HandlerReviewRosterBudget", "NodeReviewRosterBudgetCompute"]
