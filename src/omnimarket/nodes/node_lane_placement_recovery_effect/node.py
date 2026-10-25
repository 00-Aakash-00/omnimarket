# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Thin lane placement recovery node; receipt and lane queue behavior lives in its handler."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .handlers.handler_claude_account_limit import HandlerClaudeAccountLimit
from .handlers.handler_lane_engine_failover import (
    HandlerLaneEngineFailover,
    engine_admission,
    engine_readings,
    host_admission,
    initialize_receipt,
    parse_route,
    tick_request,
)
from .handlers.handler_lane_placement_recovery import HandlerLanePlacementRecovery
from .models.model_claude_account_limit import (
    ModelClaudeAccountLimitRequest,
    ModelClaudeAccountLimitResult,
)
from .models.model_lane_engine_failover import (
    ModelLaneEngineFailoverRequest,
    ModelLaneEngineFailoverResult,
)


class NodeLanePlacementRecoveryEffect:
    """Local entry point for the operations declared in contract.yaml."""

    contract_path = Path(__file__).with_name("contract.yaml")

    def __init__(self, runtime: Mapping[str, Any]) -> None:
        self.handler = HandlerLanePlacementRecovery(runtime)
        self.account_handler = HandlerClaudeAccountLimit(runtime)
        self.engine_handler = HandlerLaneEngineFailover(runtime)
        self.runtime = runtime

    def handle(
        self, request: ModelClaudeAccountLimitRequest | ModelLaneEngineFailoverRequest
    ) -> ModelClaudeAccountLimitResult | ModelLaneEngineFailoverResult:
        if isinstance(request, ModelLaneEngineFailoverRequest):
            return self.engine_handler.handle(request)
        return self.account_handler.handle(request)

    def execute(self, args: Any, **kwargs: Any) -> int:
        request = ModelLaneEngineFailoverRequest(
            args, self.runtime["execute_once"], kwargs
        )
        return self.engine_handler.handle(request).exit_code

    engine_readings = staticmethod(engine_readings)
    engine_admission = staticmethod(engine_admission)
    host_admission = staticmethod(host_admission)
    initialize_receipt = staticmethod(initialize_receipt)
    parse_route = staticmethod(parse_route)
    tick_request = staticmethod(tick_request)

    def placement_wait(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler.placement_wait(*args, **kwargs)

    def until_placed(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler.until_placed(*args, **kwargs)

    def end_unplaced(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler.end_unplaced(*args, **kwargs)

    def without_queued_no_host(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler.without_queued_no_host(*args, **kwargs)

    def requeue_lost_placements(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler.requeue_lost_placements(*args, **kwargs)

    def placement_refusals(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler.placement_refusals(*args, **kwargs)
