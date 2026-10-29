# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Crash, concurrent delivery and immutable-terminal checks on real local state."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID

import pytest
from omnibase_core.enums.enum_model_swap_validation_failure import (
    EnumModelSwapValidationFailure,
)
from omnibase_core.models.model_swap_validation import (
    ModelModelSwapValidationRequest,
    ModelModelSwapValidationResult,
)
from pydantic import ValidationError

from omnimarket.nodes.node_model_swap_validation_effect.handlers.handler_model_swap_validation import (
    HandlerModelSwapValidation,
    load_contract,
)
from omnimarket.nodes.node_model_swap_validation_effect.handlers.terminal_state import (
    TerminalState,
)

pytestmark = pytest.mark.unit
_CHAINS = Path(__file__).resolve().parents[3] / "golden_chains/model_swap_validation"


def request() -> ModelModelSwapValidationRequest:
    raw = json.loads((_CHAINS / "chain_positive_recommend.json").read_text())["request"]
    return ModelModelSwapValidationRequest.model_validate(raw)


def completed(req: ModelModelSwapValidationRequest) -> ModelModelSwapValidationResult:
    return ModelModelSwapValidationResult(
        correlation_id=req.correlation_id,
        status="completed",
        tool_commit="a1fed146ed738fd50be224716ad6139e35d6d173",
        image_digest="sha256:" + "b" * 64,
        decision="recommend",
        recommended_model=req.candidate_model,
        lower_bound=0.95,
        judge_mode=req.judge_mode,
        quality_floor=req.quality_floor,
    )


class Validator:
    validator_id = "example-validator@pinned-version"

    def __init__(self, *, delay: float = 0, error: BaseException | None = None) -> None:
        self.calls = 0
        self.recoveries: list[UUID] = []
        self.delay = delay
        self.error = error

    async def validate(
        self, req: ModelModelSwapValidationRequest
    ) -> ModelModelSwapValidationResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        await asyncio.sleep(self.delay)
        return completed(req)

    async def recover(self, correlation_id: UUID) -> None:
        self.recoveries.append(correlation_id)


async def test_concurrent_duplicate_handlers_start_one_validation(
    tmp_path: Path,
) -> None:
    validator = Validator(delay=0.03)
    first = HandlerModelSwapValidation(validator=validator, state_directory=tmp_path)
    second = HandlerModelSwapValidation(validator=validator, state_directory=tmp_path)
    results = await asyncio.gather(
        *(handler.handle(request()) for handler in (first, second, first, second))
    )
    assert validator.calls == 1
    assert len({result.model_dump_json() for result in results}) == 1


async def test_stored_terminal_survives_new_handler_and_failed_publication(
    tmp_path: Path,
) -> None:
    first_provider = Validator()
    first = HandlerModelSwapValidation(
        validator=first_provider, state_directory=tmp_path
    )
    original = await first.handle(request())
    second_provider = Validator(
        error=AssertionError("stored result must not run provider")
    )
    restarted = HandlerModelSwapValidation(
        validator=second_provider, state_directory=tmp_path
    )
    repeated = await restarted.handle_async(request())
    assert len(repeated.events) == 1
    assert repeated.events[0].model_dump_json() == original.model_dump_json()
    assert first_provider.calls == 1
    assert second_provider.calls == 0


class ProcessCrash(BaseException):
    """A simulated process death that bypasses ordinary exception handling."""


async def test_crash_after_start_intent_never_restarts_provider(tmp_path: Path) -> None:
    provider = Validator(error=ProcessCrash())
    crashed = HandlerModelSwapValidation(validator=provider, state_directory=tmp_path)
    with pytest.raises(ProcessCrash):
        await crashed.handle(request())
    resumed = HandlerModelSwapValidation(validator=provider, state_directory=tmp_path)
    result = await resumed.handle(request())
    assert result.failure_reason == EnumModelSwapValidationFailure.TIMEOUT
    assert provider.calls == 1
    assert provider.recoveries == [request().correlation_id]
    assert (
        await resumed.handle(request())
    ).model_dump_json() == result.model_dump_json()


@pytest.mark.parametrize("intent", [False, True])
async def test_crash_before_or_after_intent_is_fenced(
    tmp_path: Path, intent: bool
) -> None:
    req = request()
    state = TerminalState(tmp_path)
    fence, _, _ = state.claim(
        req.correlation_id, state.request_hash(req.model_dump(mode="json"))
    )
    if intent:
        state.store_start_intent(req.correlation_id, fence)
    provider = Validator()
    resumed = HandlerModelSwapValidation(validator=provider, state_directory=tmp_path)
    result = await resumed.handle(req)
    assert provider.calls == (0 if intent else 1)
    assert result.status == ("failed" if intent else "completed")
    assert provider.recoveries == ([req.correlation_id] if intent else [])


async def test_timeout_stops_provider_and_stores_one_terminal(tmp_path: Path) -> None:
    provider = Validator(delay=10)
    handler = HandlerModelSwapValidation(
        validator=provider, state_directory=tmp_path, timeout_seconds=0.01
    )
    result = await handler.handle(request())
    assert result.failure_reason == EnumModelSwapValidationFailure.TIMEOUT
    assert provider.recoveries == [request().correlation_id]
    assert (
        await handler.handle(request())
    ).model_dump_json() == result.model_dump_json()
    assert provider.calls == 1


async def test_missing_repository_refuses_before_provider(tmp_path: Path) -> None:
    provider = Validator()
    handler = HandlerModelSwapValidation(validator=provider, state_directory=tmp_path)
    result = await handler.handle(
        request().model_copy(update={"work_unit_repository": None})
    )
    assert result.failure_reason == EnumModelSwapValidationFailure.REPOSITORY_REFUSED
    assert provider.calls == 0


async def test_correlation_identity_cannot_be_reused_for_changed_request(
    tmp_path: Path,
) -> None:
    provider = Validator()
    handler = HandlerModelSwapValidation(validator=provider, state_directory=tmp_path)
    await handler.handle(request())
    with pytest.raises(ValueError, match="different request"):
        await handler.handle(request().model_copy(update={"task_type": "changed"}))
    assert provider.calls == 1


async def test_provider_error_does_not_leak_secret_detail(tmp_path: Path) -> None:
    provider = Validator(error=RuntimeError("sentinel-provider-secret"))
    handler = HandlerModelSwapValidation(validator=provider, state_directory=tmp_path)
    result = await handler.handle(request())
    assert result.failure_reason == EnumModelSwapValidationFailure.TOOL_FAILED
    assert "sentinel-provider-secret" not in result.model_dump_json()


def test_stale_owner_cannot_start_or_replace_terminal(tmp_path: Path) -> None:
    state = TerminalState(tmp_path)
    req = request()
    digest = state.request_hash(req.model_dump(mode="json"))
    first, _, _ = state.claim(req.correlation_id, digest)
    second, _, _ = state.claim(req.correlation_id, digest)
    assert second > first
    with pytest.raises(ValueError, match="fence"):
        state.store_start_intent(req.correlation_id, first)
    state.store_terminal(req.correlation_id, second, completed(req).model_dump_json())
    with pytest.raises(ValueError, match="fence"):
        state.store_terminal(req.correlation_id, second, "replacement")


def test_contract_topics_commands_and_chain_inputs_match_platform_models() -> None:
    contract = load_contract()
    subscribe = contract["event_bus"]["subscribe_topics"]
    publish = contract["event_bus"]["publish_topics"]
    assert len(set(subscribe + publish)) == 3
    assert set(contract["analysis_commands"]).isdisjoint(
        {"apply", "rollback", "watch", "export", "corpus", "drift"}
    )
    paths = sorted(_CHAINS.glob("chain_*.json"))
    assert len(paths) == 12
    for chain in paths:
        fixture = json.loads(chain.read_text())
        ModelModelSwapValidationRequest.model_validate(fixture["request"])
        assert fixture["expected"]["evidence_only"] is True


@pytest.mark.parametrize("floor", [0.8, 1.0])
def test_request_rejects_quality_policy_boundaries(floor: float) -> None:
    payload = request().model_dump(mode="json")
    payload["quality_floor"] = floor
    with pytest.raises(ValidationError):
        ModelModelSwapValidationRequest.model_validate(payload)


@pytest.mark.parametrize("phase", ["claimed", "started", "stored"])
async def test_actual_process_death_releases_owner_without_second_start(
    tmp_path: Path, phase: str
) -> None:
    import subprocess
    import sys

    script = r"""
import asyncio
import os
import sys
from pathlib import Path
from omnimarket.nodes.node_model_swap_validation_effect.handlers.handler_model_swap_validation import HandlerModelSwapValidation
from omnimarket.nodes.node_model_swap_validation_effect.handlers.terminal_state import TerminalState
from tests.unit.nodes.node_model_swap_validation_effect.test_handler_model_swap_validation import request, completed

root = Path(sys.argv[1])
phase = sys.argv[2]
req = request()

class ChildValidator:
    validator_id = "process-crash-fixture@pinned"
    async def validate(self, req):
        with (root / "starts.txt").open("a") as stream:
            stream.write("start\n")
            stream.flush()
            os.fsync(stream.fileno())
        if phase == "started":
            os._exit(23)
        return completed(req)

if phase == "claimed":
    state = TerminalState(root)
    state.claim(req.correlation_id, state.request_hash(req.model_dump(mode="json")))
else:
    asyncio.run(HandlerModelSwapValidation(validator=ChildValidator(), state_directory=root).handle(req))
os._exit(23)
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), phase],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert child.returncode == 23, child.stderr.decode()
    provider = Validator()
    resumed = HandlerModelSwapValidation(validator=provider, state_directory=tmp_path)
    result = await resumed.handle(request())
    starts = tmp_path / "starts.txt"
    child_starts = len(starts.read_text().splitlines()) if starts.exists() else 0
    assert child_starts + provider.calls == 1
    assert result.status == ("failed" if phase == "started" else "completed")
    assert provider.calls == (1 if phase == "claimed" else 0)


def test_default_runtime_handler_reads_nonsecret_contract_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = load_contract()
    contract["descriptor"]["timeout_ms"] = 1234
    contract["rightmodeler"] = {"replay_root": str(tmp_path / "replays")}
    overlay = tmp_path / "rendered-contract.json"
    overlay.write_text(json.dumps(contract))
    monkeypatch.setenv("ONEX_MODEL_SWAP_CONTRACT_PATH", str(overlay))
    monkeypatch.setenv("ONEX_MODEL_SWAP_STATE_DIRECTORY", str(tmp_path / "state"))
    handler = HandlerModelSwapValidation(validator=Validator())
    assert handler._timeout == 1.234
    assert handler._contract["rightmodeler"]["replay_root"] == str(tmp_path / "replays")


def test_explicit_contract_path_takes_precedence_over_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "explicit.json"
    path.write_text(json.dumps(load_contract()))
    monkeypatch.setenv(
        "ONEX_MODEL_SWAP_CONTRACT_PATH", str(tmp_path / "does-not-exist")
    )
    HandlerModelSwapValidation(
        validator=Validator(), state_directory=tmp_path / "state", contract_path=path
    )


@pytest.mark.parametrize(
    "boundary",
    [
        "event_bus",
        "runtime_dispatch",
        "published_events",
        "analysis_commands",
        "analysis_flags",
        "tool_pin",
    ],
)
def test_operator_overlay_cannot_replace_runtime_or_sandbox_boundaries(
    tmp_path: Path, boundary: str
) -> None:
    contract = load_contract()
    contract[boundary] = {}
    path = tmp_path / "altered-contract.json"
    path.write_text(json.dumps(contract))
    with pytest.raises(ValueError, match=f"must preserve {boundary}"):
        HandlerModelSwapValidation(
            validator=Validator(),
            state_directory=tmp_path / "state",
            contract_path=path,
        )
