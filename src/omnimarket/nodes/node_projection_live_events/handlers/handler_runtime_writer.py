# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Runtime dispatch entrypoint for the live events projection."""

from typing import Any

from omnimarket.handlers.handler_projection_runtime_writer import (
    RuntimeProjectionWriter,
)
from omnimarket.nodes.node_projection_live_events.handlers.handler_live_events import (
    HandlerLiveEventsProjectionRunner,
)
from omnimarket.projection.envelope import strip_runner_injected_keys


class LiveEventsProjectionWriter(RuntimeProjectionWriter):
    """Apply the existing writer once for each runtime delivery."""

    runner_type = HandlerLiveEventsProjectionRunner

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """Keep runtime bookkeeping out of the stored raw event payload."""
        return super().handle(strip_runner_injected_keys(request))
