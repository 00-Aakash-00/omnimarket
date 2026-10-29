# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Protocol-driven evidence collection with durable, fenced terminal delivery."""

from __future__ import annotations

import asyncio
import importlib
import os
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import yaml
from omnibase_core.enums.enum_model_swap_validation_failure import (
    EnumModelSwapValidationFailure,
)
from omnibase_core.enums.enum_model_swap_validation_status import (
    EnumModelSwapValidationStatus,
)
from omnibase_core.models.dispatch.model_handler_output import ModelHandlerOutput
from omnibase_core.models.model_swap_validation import (
    ModelModelSwapValidationRequest,
    ModelModelSwapValidationResult,
)
from omnibase_spi.protocols.validation.protocol_model_swap_validator import (
    ProtocolModelSwapValidator,
)

from omnimarket.nodes.node_model_swap_validation_effect.handlers.terminal_state import (
    TerminalState,
)
from omnimarket.nodes.node_model_swap_validation_effect.models.model_model_swap_validation import (
    terminal_event,
)

_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contract.yaml"


def load_contract(path: Path = _CONTRACT_PATH) -> dict[str, Any]:
    """Read runtime configuration and topics from the node's single contract."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("validation contract must be a mapping")
    return cast(dict[str, Any], raw)


class HandlerModelSwapValidation:
    """Invoke one validation provider at most once per immutable request identity.

    The platform protocol deliberately has one method. Start intent therefore
    fences the complete provider invocation, including its replay-set preflight;
    the provider still refuses invalid inputs before launching its sandbox.
    The per-correlation OS lock releases on process death, while SQLite retains
    the fence, start intent and immutable validated terminal across restarts.
    """

    def __init__(
        self,
        *,
        validator: ProtocolModelSwapValidator | None = None,
        state_directory: Path | None = None,
        timeout_seconds: float | None = None,
        contract_path: Path | None = None,
        recover: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        if contract_path is None:
            configured_contract = os.environ.get(
                "ONEX_MODEL_SWAP_CONTRACT_PATH", ""
            ).strip()
            contract_path = (
                Path(configured_contract) if configured_contract else _CONTRACT_PATH
            )
        self._contract = load_contract(contract_path)
        packaged = load_contract()
        for boundary in (
            "event_bus",
            "runtime_dispatch",
            "published_events",
            "analysis_commands",
            "analysis_flags",
            "tool_pin",
        ):
            if self._contract.get(boundary) != packaged.get(boundary):
                raise ValueError(f"operator contract must preserve {boundary}")
        if state_directory is None:
            configured = os.environ.get("ONEX_MODEL_SWAP_STATE_DIRECTORY", "")
            if not configured:
                raise ValueError(
                    "ONEX_MODEL_SWAP_STATE_DIRECTORY must name durable state"
                )
            state_directory = Path(configured)
        self._state = TerminalState(state_directory)
        self._timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else float(self._contract["descriptor"]["timeout_ms"]) / 1000
        )
        if self._timeout <= 0:
            raise ValueError("validation timeout must be positive")
        self._validator = validator
        self._recover = recover

    def _provider(self) -> ProtocolModelSwapValidator:
        if self._validator is None:
            binding: Mapping[str, str] = self._contract["validator"]
            module = importlib.import_module(binding["module"])
            provider_type = getattr(module, binding["class"])
            provider = provider_type.from_contract(
                self._contract, state_directory=self._state.directory
            )
            if not isinstance(provider, ProtocolModelSwapValidator):
                raise TypeError(
                    "contract validator does not satisfy the platform protocol"
                )
            self._validator = provider
        return self._validator

    @staticmethod
    def _failure(
        request: ModelModelSwapValidationRequest,
        reason: EnumModelSwapValidationFailure,
        *,
        code: str | None = None,
    ) -> ModelModelSwapValidationResult:
        return ModelModelSwapValidationResult(
            correlation_id=request.correlation_id,
            status=EnumModelSwapValidationStatus.FAILED,
            failure_reason=reason,
            judge_mode=request.judge_mode,
            quality_floor=request.quality_floor,
            tool_error_code=code,
        )

    async def _reconcile(self, correlation_id: UUID) -> None:
        recover = self._recover
        if recover is None and self._validator is not None:
            candidate = getattr(self._validator, "recover", None)
            if callable(candidate):
                recover = cast(Callable[[UUID], Awaitable[None]], candidate)
        if recover is not None:
            await asyncio.wait_for(recover(correlation_id), timeout=self._timeout)

    async def handle(
        self, request: ModelModelSwapValidationRequest
    ) -> ModelModelSwapValidationResult:
        request_hash = self._state.request_hash(request.model_dump(mode="json"))
        async with self._state.ownership(request.correlation_id):
            fence, stored, interrupted = self._state.claim(
                request.correlation_id, request_hash
            )
            if stored is not None:
                return terminal_event(
                    ModelModelSwapValidationResult.model_validate_json(stored)
                )
            if interrupted:
                try:
                    # Reconstruct the contract-bound provider solely to stop or
                    # reconcile its previous run. Never invoke validate again.
                    self._provider()
                    await self._reconcile(request.correlation_id)
                    code = "interrupted_validation"
                except Exception:
                    code = "recovery_failed"
                result = self._failure(
                    request, EnumModelSwapValidationFailure.TIMEOUT, code=code
                )
            elif request.work_unit_repository is None:
                result = self._failure(
                    request, EnumModelSwapValidationFailure.REPOSITORY_REFUSED
                )
            else:
                self._state.store_start_intent(request.correlation_id, fence)
                try:
                    result = await asyncio.wait_for(
                        self._provider().validate(request), timeout=self._timeout
                    )
                    if result.correlation_id != request.correlation_id:
                        result = self._failure(
                            request, EnumModelSwapValidationFailure.SCHEMA_MISMATCH
                        )
                except TimeoutError:
                    with suppress(Exception):
                        await self._reconcile(request.correlation_id)
                    result = self._failure(
                        request, EnumModelSwapValidationFailure.TIMEOUT
                    )
                except asyncio.CancelledError:
                    try:
                        await self._reconcile(request.correlation_id)
                    finally:
                        result = self._failure(
                            request, EnumModelSwapValidationFailure.TIMEOUT
                        )
                        self._state.store_terminal(
                            request.correlation_id, fence, result.model_dump_json()
                        )
                    raise
                except Exception:
                    result = self._failure(
                        request,
                        EnumModelSwapValidationFailure.TOOL_FAILED,
                        code="validator_error",
                    )
            terminal = terminal_event(result)
            self._state.store_terminal(
                request.correlation_id, fence, terminal.model_dump_json()
            )
            return terminal

    async def handle_async(
        self, request: ModelModelSwapValidationRequest
    ) -> ModelHandlerOutput[None]:
        """Let runtime publishing derive exactly one topic from terminal type."""
        result = await self.handle(request)
        return ModelHandlerOutput.for_effect(
            input_envelope_id=uuid4(),
            correlation_id=request.correlation_id,
            handler_id="node_model_swap_validation_effect.validate",
            events=(result,),
        )
