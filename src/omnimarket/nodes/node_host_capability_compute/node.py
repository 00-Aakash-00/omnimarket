# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Declarative entry point for the lab host parity probe's capability half."""

from pathlib import Path

from .handlers.handler_host_capability import HandlerHostCapability
from .models.model_host_capability import (
    ModelHostCapabilityRequest,
    ModelHostCapabilityResult,
)


class NodeHostCapabilityCompute:
    contract_path = Path(__file__).with_name("contract.yaml")

    def __init__(self) -> None:
        self.handler = HandlerHostCapability()

    def handle(self, request: ModelHostCapabilityRequest) -> ModelHostCapabilityResult:
        return self.handler.handle(request)
