# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Evidence-only adapter over the qualified, pinned analysis CLI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from omnibase_core.models.model_swap_validation import (
    ModelModelSwapValidationRequest,
    ModelModelSwapValidationResult,
)
from omnibase_spi.protocols.services import ProtocolSecretStore

from omnimarket.inference.secret_store_resolver import secret_store_from_config_path
from omnimarket.nodes.node_llm_delegation_call_effect import HandlerBoundedModelRequest
from omnimarket.routing.delegation_backend_resolution import (
    ModelResolvedDelegationBackend,
)

from .container import (
    TOOL_COMMIT,
    EgressMonitorError,
    GatewayRelay,
    RunFence,
    SandboxCancelledError,
    SandboxError,
    SandboxRunner,
    ToolRun,
    write_synthetic_repository,
)
from .gateway import GatewaySession, ModelPrice
from .replay_set import ReplaySetError, canonical_json, load_replay_set
from .report import ReportError, parse_report

RunTool = Callable[
    [ModelModelSwapValidationRequest, Path, Path, GatewaySession], ToolRun
]


def tool_error_code(stderr: str) -> str | None:
    """Keep the final structured error after any JSONL progress warnings."""
    documents: list[Any] = []
    with suppress(json.JSONDecodeError):
        documents.append(json.loads(stderr))
    if not documents:
        for line in stderr.splitlines():
            with suppress(json.JSONDecodeError):
                documents.append(json.loads(line))
    code = None
    for document in documents:
        if (
            isinstance(document, dict)
            and document.get("event", "error") == "error"
            and document.get("type") != "warning"
            and isinstance(document.get("code"), str)
        ):
            code = document["code"]
    return code


class ValidatorRightModeler:
    """All tool flags, sandbox policy and report normalization live in the adapter."""

    def __init__(
        self,
        *,
        replay_root: Path,
        state_directory: Path,
        tool_pin: str | None,
        image_digest: str | None,
        prices: Mapping[str, ModelPrice],
        stub_judge_model: str,
        judge_backends: Mapping[str, ModelResolvedDelegationBackend] | None = None,
        candidate_backend: ModelResolvedDelegationBackend | None = None,
        secret_store: ProtocolSecretStore | None = None,
        run_tool: RunTool | None = None,
        runtime: Mapping[str, Any] | None = None,
        refused_repositories: tuple[str, ...] = (),
    ) -> None:
        self.replay_root, self.state_directory = replay_root, state_directory
        self.tool_pin, self.image_digest, self.prices = tool_pin, image_digest, prices
        self.stub_judge_model, self.judge_backends = (
            stub_judge_model,
            judge_backends or {},
        )
        self.candidate_backend, self.secret_store = candidate_backend, secret_store
        self.runtime, self._run_override = dict(runtime or {}), run_tool
        self.refused_repositories = refused_repositories
        self._fences: dict[UUID, RunFence] = {}
        self._fences_lock = threading.Lock()

    def _fence(self, correlation_id: UUID) -> RunFence:
        with self._fences_lock:
            return self._fences.setdefault(correlation_id, RunFence())

    @property
    def validator_id(self) -> str:
        return f"rightmodeler:{self.tool_pin or 'unpinned'}"

    @classmethod
    def from_contract(
        cls, contract: Mapping[str, Any], *, state_directory: Path
    ) -> ValidatorRightModeler:
        """Bind only operator overlays; no model, endpoint, price or credential default."""
        config = contract.get("rightmodeler", {})
        store_path = config.get("secret_resolver_config_path")
        return cls(
            replay_root=Path(
                config.get("replay_root", state_directory / "replay_sets")
            ),
            state_directory=state_directory,
            tool_pin=contract.get("tool_pin", {}).get("commit"),
            image_digest=config.get("image_digest"),
            prices={
                model: ModelPrice(**price)
                for model, price in config.get("prices", {}).items()
            },
            stub_judge_model=config.get("stub_judge_model", ""),
            judge_backends={
                key: ModelResolvedDelegationBackend.model_validate(value)
                for key, value in config.get("judge_backends", {}).items()
            },
            candidate_backend=ModelResolvedDelegationBackend.model_validate(
                config["candidate_backend"]
            )
            if config.get("candidate_backend")
            else None,
            secret_store=secret_store_from_config_path(store_path)
            if store_path
            else None,
            runtime=config.get("runtime", {}),
            refused_repositories=tuple(
                contract.get("work_unit_scoping", {}).get("refused_repositories", ())
            ),
        )

    def _failure(
        self,
        request: ModelModelSwapValidationRequest,
        reason: str,
        *,
        code: str | None = None,
        run: ToolRun | None = None,
        gateway: GatewaySession | None = None,
    ) -> ModelModelSwapValidationResult:
        calls = gateway.calls if gateway else ()
        return ModelModelSwapValidationResult.model_validate(
            {
                "correlation_id": request.correlation_id,
                "status": "failed",
                "failure_reason": reason,
                "tool_commit": self.tool_pin
                if self.tool_pin and re.fullmatch(r"[0-9a-f]{40}", self.tool_pin)
                else None,
                "image_digest": self.image_digest
                if self.image_digest
                and re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_digest)
                else None,
                "quality_floor": request.quality_floor,
                "judge_mode": request.judge_mode,
                "tool_error_code": code,
                "exit_code": run.exit_code if run else None,
                "gateway_calls": calls,
                "provider_reported_cost_usd": sum(
                    c.cost_usd or 0
                    for c in calls
                    if c.cost_measurement_source == "provider_reported_charge"
                ),
                "estimated_cost_usd": sum(
                    c.cost_usd or 0
                    for c in calls
                    if c.cost_measurement_source == "estimated_list_price"
                ),
            }
        )

    async def validate(
        self, request: ModelModelSwapValidationRequest
    ) -> ModelModelSwapValidationResult:
        self._fence(request.correlation_id)
        try:
            return await asyncio.to_thread(self._validate, request)
        except asyncio.CancelledError:
            self._fence(request.correlation_id).cancelled.set()
            await asyncio.shield(self.recover(request.correlation_id))
            raise

    def _validate(
        self, request: ModelModelSwapValidationRequest
    ) -> ModelModelSwapValidationResult:
        if self._fence(request.correlation_id).cancelled.is_set():
            return self._failure(request, "timeout")
        repository = request.work_unit_repository
        refused = (
            "sha256:"
            + hashlib.sha256(
                (repository or "").split("/")[-1].lower().encode()
            ).hexdigest()
        )
        if (
            not repository
            or not repository.strip()
            or refused in self.refused_repositories
        ):
            return self._failure(request, "repository_refused")
        if (
            self.tool_pin != TOOL_COMMIT
            or not self.image_digest
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_digest)
        ):
            return self._failure(request, "unpinned_tool")
        try:
            replay = load_replay_set(
                self.replay_root,
                request.replay_set_digest,
                task_type=request.task_type,
                reference_model=request.reference_model,
                recorded=request.answer_source == "recorded",
            )
        except ReplaySetError:
            return self._failure(request, "replay_set_missing")
        backend = self.judge_backends.get(request.judge_backend_id or "")
        judge_model = (
            self.stub_judge_model
            if request.judge_mode == "stub"
            else backend.model_id
            if backend
            else ""
        )
        if (
            not judge_model
            or len({request.reference_model, request.candidate_model, judge_model}) != 3
        ):
            return self._failure(request, "no_neutral_judge")
        if judge_model.split("/")[0] in {
            request.reference_model.split("/")[0],
            request.candidate_model.split("/")[0],
        }:
            return self._failure(request, "no_neutral_judge")
        try:
            reference, candidate, judge = (
                self.prices[model]
                for model in (
                    request.reference_model,
                    request.candidate_model,
                    judge_model,
                )
            )
        except KeyError:
            return self._failure(request, "needs_input", code="pricing_missing")
        tokens_in = sum(case["usage"]["prompt_tokens"] for case in replay.cases)
        tokens_out = sum(case["usage"]["completion_tokens"] for case in replay.cases)
        if candidate.cost(tokens_in, tokens_out) >= reference.cost(
            tokens_in, tokens_out
        ):
            return self._failure(request, "needs_input", code="candidate_not_cheaper")
        if (
            request.judge_mode == "backend"
            and (backend is None or self.secret_store is None)
        ) or (
            request.answer_source == "live"
            and (
                self.candidate_backend is None
                or self.candidate_backend.model_id != request.candidate_model
                or self.secret_store is None
            )
        ):
            return self._failure(request, "needs_input", code="backend_not_configured")
        if self._run_override is None:
            try:
                docker = tuple(self.runtime.get("docker", ("docker",)))
                inspected = subprocess.run(
                    [
                        *docker,
                        "image",
                        "inspect",
                        self.image_digest,
                        "--format",
                        '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if inspected.stdout.strip() != self.tool_pin:
                    raise SandboxError("image revision differs from the contract pin")
                if "@sha256:" not in str(self.runtime.get("monitor_image", "")):
                    raise SandboxError("monitor image must be pinned")
            except (SandboxError, OSError, subprocess.SubprocessError):
                return self._failure(request, "unpinned_tool")
        transports = {}
        if self.secret_store is not None:
            if backend is not None:
                transports["judge"] = HandlerBoundedModelRequest(
                    backend, secret_store=self.secret_store
                )
            if self.candidate_backend is not None:
                transports["candidate"] = HandlerBoundedModelRequest(
                    self.candidate_backend, secret_store=self.secret_store
                )

        def upstream(role: str, body: dict[str, Any]) -> dict[str, Any]:
            self._fence(request.correlation_id).check()
            return transports[role].handle(body)

        maximum = min(
            [4096]
            + [b.max_tokens for b in (backend, self.candidate_backend) if b is not None]
        )
        self.state_directory.mkdir(parents=True, exist_ok=True)
        with (
            GatewaySession(
                request,
                replay,
                ledger_path=self.state_directory
                / "budgets"
                / f"{request.correlation_id}.sqlite3",
                reference_price=reference,
                candidate_price=candidate,
                judge_price=judge,
                judge_model=judge_model,
                upstream=upstream if transports else None,
                max_output_tokens=maximum,
                host="127.0.0.1" if self._run_override else "0.0.0.0",
            ) as gateway,
            tempfile.TemporaryDirectory(
                prefix="model-swap-", dir=self.state_directory
            ) as temporary,
        ):
            work = Path(temporary)
            inputs, synthetic = work / "replay", work / "synthetic"
            inputs.mkdir()
            for filename, data in replay.files.items():
                (inputs / filename).write_bytes(data)
            (inputs / "policy.json").write_bytes(
                canonical_json(
                    {
                        "qualityFloor": request.quality_floor,
                        "shortlistTop": 1,
                        "allowModels": [request.candidate_model],
                    }
                )
            )
            write_synthetic_repository(
                synthetic, request.task_type, request.reference_model
            )
            try:
                run = (
                    self._run_override(request, inputs, synthetic, gateway)
                    if self._run_override
                    else self._run_tool(request, inputs, synthetic, gateway)
                )
            except SandboxCancelledError:
                return self._failure(request, "timeout", gateway=gateway)
            except EgressMonitorError:
                return self._failure(
                    request,
                    "egress_violation",
                    code="monitor_unavailable",
                    gateway=gateway,
                )
            except SandboxError:
                return self._failure(
                    request, "unpinned_tool", code="sandbox_refused", gateway=gateway
                )
            except (OSError, subprocess.SubprocessError):
                return self._failure(request, "tool_failed", gateway=gateway)
            if run.forbidden_destinations:
                return self._failure(
                    request, "egress_violation", run=run, gateway=gateway
                )
            if gateway.no_neutral_judge:
                return self._failure(
                    request, "no_neutral_judge", run=run, gateway=gateway
                )
            if gateway.egress_violation:
                return self._failure(
                    request, "egress_violation", run=run, gateway=gateway
                )
            if run.timed_out:
                return self._failure(request, "timeout", run=run, gateway=gateway)
            if gateway.budget_refused or run.exit_code == 3:
                return self._failure(request, "spend_cap", run=run, gateway=gateway)
            if gateway.schema_mismatch:
                return self._failure(
                    request, "schema_mismatch", run=run, gateway=gateway
                )
            if gateway.tool_failed:
                return self._failure(request, "tool_failed", run=run, gateway=gateway)
            error_code = tool_error_code(run.stderr)
            if run.exit_code == 2:
                return self._failure(
                    request,
                    "no_neutral_judge"
                    if error_code == "no_neutral_judge"
                    else "needs_input",
                    code=error_code,
                    run=run,
                    gateway=gateway,
                )
            if run.exit_code not in {0, 1}:
                return self._failure(
                    request, "tool_failed", code=error_code, run=run, gateway=gateway
                )
            try:
                evidence = parse_report(run.report, request, gateway)
            except ReportError:
                return self._failure(
                    request, "schema_mismatch", run=run, gateway=gateway
                )
            return ModelModelSwapValidationResult.model_validate(
                {
                    "correlation_id": request.correlation_id,
                    "status": "completed",
                    "tool_commit": self.tool_pin,
                    "image_digest": self.image_digest,
                    "quality_floor": request.quality_floor,
                    "judge_mode": request.judge_mode,
                    "exit_code": run.exit_code,
                    **evidence,
                }
            )

    def _run_tool(
        self,
        request: ModelModelSwapValidationRequest,
        replay: Path,
        synthetic: Path,
        gateway: GatewaySession,
    ) -> ToolRun:
        docker = tuple(self.runtime.get("docker", ("docker",)))
        monitor_image = str(self.runtime.get("monitor_image", ""))
        relay = GatewayRelay(
            docker=docker,
            monitor_image=monitor_image,
            correlation_id=request.correlation_id,
            host_address=str(
                self.runtime.get("gateway_host_address", "host.docker.internal")
            ),
            host_port=gateway.port,
            fence=self._fence(request.correlation_id),
        )
        try:
            gateway_ip = relay.start()
            runner = SandboxRunner(
                image_digest=self.image_digest or "",
                monitor_image=monitor_image,
                network=relay.network,
                gateway_ip=gateway_ip,
                gateway_port=8080,
                docker=docker,
                tool_commit=self.tool_pin or "",
                timeout_seconds=float(self.runtime.get("timeout_seconds", 300)),
                fence=self._fence(request.correlation_id),
            )
            return runner.run(request, replay, synthetic, gateway.token)
        finally:
            relay.stop()

    async def recover(self, correlation_id: UUID) -> None:
        """Stop any prior sandbox and relay; never launch a replacement."""
        self._fence(correlation_id).cancelled.set()
        if self._run_override is not None:
            return
        await asyncio.to_thread(self._recover_sync, correlation_id)

    def _recover_sync(self, correlation_id: UUID) -> None:
        docker = tuple(self.runtime.get("docker", ("docker",)))
        with self._fence(correlation_id).lock:
            for name in (
                *SandboxRunner.names(correlation_id),
                f"rmv-gateway-{correlation_id.hex}",
            ):
                subprocess.run(
                    [*docker, "rm", "--force", name], capture_output=True, timeout=30
                )
            subprocess.run(
                [*docker, "network", "rm", f"rmv-network-{correlation_id.hex}"],
                capture_output=True,
                timeout=30,
            )
