# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Declarative entry point for the link read history."""

from pathlib import Path

from .handlers.handler_link_history import HandlerLinkHistory
from .models.model_link_history import ModelLinkHistoryRequest, ModelLinkHistoryResult


class NodeLinkHistoryEffect:
    contract_path = Path(__file__).with_name("contract.yaml")

    def __init__(self) -> None:
        self.handler = HandlerLinkHistory()

    def handle(self, request: ModelLinkHistoryRequest) -> ModelLinkHistoryResult:
        return self.handler.handle(request)
