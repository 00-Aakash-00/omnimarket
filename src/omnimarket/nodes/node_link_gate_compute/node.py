# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Declarative entry point for the ssh link gate."""

from pathlib import Path

from .handlers.handler_link_gate import HandlerLinkGate
from .models.model_link_gate import ModelLinkGateRequest, ModelLinkGateVerdict


class NodeLinkGateCompute:
    contract_path = Path(__file__).with_name("contract.yaml")

    def __init__(self) -> None:
        self.handler = HandlerLinkGate()

    def handle(self, request: ModelLinkGateRequest) -> ModelLinkGateVerdict:
        return self.handler.handle(request)
