# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""node_named_test_run_effect -- run the tests a delegation names against its
answer, in a copy of the caller's workspace (OMN-21077).

The bus-less delegation port runs ``HandlerNamedTestRunEffect`` when a code-class
request names tests, and hands the ``ModelNamedTestRun`` it returns to the
quality gate's ``passes_existing_tests`` check.
"""

from omnimarket.nodes.node_named_test_run_effect.handlers.handler_named_test_run_effect import (
    HandlerNamedTestRunEffect,
)


class NodeNamedTestRunEffect(HandlerNamedTestRunEffect):
    """ONEX entry-point wrapper for HandlerNamedTestRunEffect."""


__all__ = ["HandlerNamedTestRunEffect", "NodeNamedTestRunEffect"]
