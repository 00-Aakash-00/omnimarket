# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Declarative entry point for the lane guard admission."""

from pathlib import Path

from .handlers.handler_lane_guard_admission import HandlerLaneGuardAdmission
from .models.model_lane_guard_admission import (
    ModelLaneGuardAdmissionRequest,
    ModelLaneGuardAdmissionVerdict,
)


class NodeLaneGuardAdmissionCompute:
    contract_path = Path(__file__).with_name("contract.yaml")

    def __init__(self) -> None:
        self.handler = HandlerLaneGuardAdmission()

    def handle(
        self, request: ModelLaneGuardAdmissionRequest
    ) -> ModelLaneGuardAdmissionVerdict:
        return self.handler.handle(request)
