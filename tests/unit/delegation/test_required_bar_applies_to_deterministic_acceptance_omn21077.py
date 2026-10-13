# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""A verifiable class's required_bar applies to deterministic acceptance on both paths."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from omnimarket.models.delegation.wire.model_quality_gate import (
    SCORE_SOURCE_COMBINED,
    SCORE_SOURCE_DETERMINISTIC_ACCEPTANCE,
    ModelQualityGateResult,
)
from omnimarket.nodes.node_delegate_skill_orchestrator.ports import (
    port_local_delegation_dispatch as port_mod,
)
from omnimarket.nodes.node_delegate_skill_orchestrator.ports.port_local_delegation_dispatch import (
    LocalDelegationDispatchPort,
)
from omnimarket.nodes.node_delegation_orchestrator.enums import EnumDelegationState
from omnimarket.nodes.node_delegation_orchestrator.handlers import (
    handler_delegation_workflow as hw,
)
from omnimarket.nodes.node_delegation_orchestrator.models import (
    ModelDelegationCompleted,
    ModelDelegationFailed,
    ModelDelegationRequest,
    ModelInferenceResponseData,
    ModelQualityGateIntent,
    ModelRoutingIntent,
)
from omnimarket.nodes.node_delegation_orchestrator.quality_bar_authority import (
    resolve_required_bar_authority,
)
from omnimarket.nodes.node_delegation_quality_gate_reducer.handlers.handler_quality_gate import (
    delta,
)
from omnimarket.nodes.node_delegation_routing_reducer.handlers.handler_delegation_routing import (
    resolve_task_class_dod_checks,
)
from omnimarket.nodes.node_delegation_routing_reducer.models.model_routing_decision import (
    ModelRoutingDecision,
)
from omnimarket.nodes.node_llm_delegation_call_effect import (
    ModelLlmDelegationCallRequest,
    ModelLlmDelegationCallResult,
)
from omnimarket.routing.delegation_backend_resolution import (
    ModelResolvedDelegationBackend,
)

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("stub_provider_quota_reader")]

PROMPT = "add two ints"
_STUB_BODY = "def add(a: int, b: int) -> int:\n    pass"
_PASS_STUB = f"### ANSWER\n{_STUB_BODY}"
_LOCAL_BACKEND = ModelResolvedDelegationBackend(
    backend_id="local-coder",
    model_id="Qwen3.6-35B-A3B",
    endpoint_ref="https://local.example/v1/chat/completions",
    tier="local",
    max_tokens=4096,
    timeout_ms=30000,
)


def _no_escalation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        port_mod, "resolve_delegation_backend", lambda *_a, **_k: _LOCAL_BACKEND
    )
    monkeypatch.setattr(port_mod, "next_eligible_tier", lambda *_a, **_k: None)
    monkeypatch.setattr(port_mod, "tier_for_backend", lambda _backend_id: "local")
    monkeypatch.setattr(
        port_mod, "resolve_task_class_max_escalations", lambda _task_type: 0
    )
    monkeypatch.setattr(hw, "next_eligible_tier", lambda *_a, **_k: None)
    monkeypatch.setattr(hw, "tier_max_retries", lambda _tier: 0)
    monkeypatch.setattr(hw, "is_free_tier", lambda _tier: False)


def _effect_returning(content: str) -> port_mod._EffectHandler:
    def _effect(
        request: ModelLlmDelegationCallRequest,
    ) -> ModelLlmDelegationCallResult:
        return ModelLlmDelegationCallResult(
            request_id=request.request_id,
            success=True,
            content=content,
            tokens_in=10,
            tokens_out=20,
            latency_ms=5,
            actual_cost_usd=Decimal("0"),
            savings_usd=Decimal("0"),
        )

    return _effect


def _dispatch(
    port: LocalDelegationDispatchPort, *, task_type: str
) -> dict[str, object]:
    return asyncio.run(
        port.dispatch(
            prompt=PROMPT,
            task_type=task_type,
            correlation_id=uuid4(),
            max_tokens=256,
            source_file_path=None,
            source_session_id=None,
            wait=True,
            execution_timeout_seconds=240,
            terminal_delivery_margin_seconds=60,
            quality_contract_mode="extend_task_class",
            acceptance_criteria=(),
            tenant_id=None,
        )
    )


def _bus_workflow(
    *, task_type: str, content: str
) -> tuple[hw.HandlerDelegationWorkflow, ModelQualityGateIntent]:
    handler = hw.HandlerDelegationWorkflow(workflows={})
    cid = uuid4()
    handler.handle_delegation_request(
        ModelDelegationRequest(
            prompt=PROMPT,
            task_type=task_type,
            correlation_id=cid,
            emitted_at=datetime.now(UTC),
        )
    )
    deterministic, heuristic = resolve_task_class_dod_checks(task_type, prompt=PROMPT)
    handler.handle_routing_decision(
        ModelRoutingDecision(
            correlation_id=cid,
            task_type=task_type,
            selected_model="fixture-model",
            selected_backend_id=uuid4(),
            selected_backend_ref="fixture-backend",
            endpoint_url="http://provider.invalid:8000",
            cost_tier="low",
            max_context_tokens=65536,
            max_tokens=2048,
            system_prompt="Return the requested artifact.",
            rationale="Controlled single-tier route for required-bar acceptance.",
            tier_name="local",
            dod_deterministic=deterministic,
            dod_heuristic=heuristic,
        )
    )
    contract = handler.workflows[cid].effective_deliverable_contract
    assert contract is not None
    events = handler.handle_inference_response(
        ModelInferenceResponseData(
            correlation_id=cid,
            content=f"{contract.render_start_marker}\n{content}",
            model_used="fixture-model",
            latency_ms=10,
        )
    )
    assert handler.workflows[cid].state is EnumDelegationState.INFERENCE_COMPLETED
    intent = next(
        event for event in events if isinstance(event, ModelQualityGateIntent)
    )
    return handler, intent


@pytest.mark.parametrize("task_type", ["code_generation", "refactor"])
def test_local_path_refuses_pass_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task_type: str
) -> None:
    _no_escalation(monkeypatch)
    bar = resolve_required_bar_authority(task_type=task_type).required_bar
    port = LocalDelegationDispatchPort(
        effect_handler=_effect_returning(_PASS_STUB),
        evidence_db_path=tmp_path / "d.sqlite",
        effect_process_boundary=False,
    )
    result = _dispatch(port, task_type=task_type)
    score = result["quality_score"]
    assert isinstance(score, int | float | str)
    assert float(score) < bar
    assert result["status"] == "failed"
    assert result["quality_gate_passed"] is False


@pytest.mark.parametrize("task_type", ["code_generation", "refactor"])
def test_bus_path_refuses_pass_stub(
    monkeypatch: pytest.MonkeyPatch, task_type: str
) -> None:
    _no_escalation(monkeypatch)
    bar = resolve_required_bar_authority(task_type=task_type).required_bar
    handler, intent = _bus_workflow(task_type=task_type, content=_STUB_BODY)
    gate_result = delta(intent.payload)
    assert gate_result.passed is True
    assert gate_result.score_source == SCORE_SOURCE_DETERMINISTIC_ACCEPTANCE
    assert gate_result.quality_score < bar
    events = handler.handle_gate_result(gate_result)
    assert not any(isinstance(event, ModelDelegationCompleted) for event in events)
    assert any(
        isinstance(event, ModelDelegationFailed | ModelRoutingIntent)
        for event in events
    )


@pytest.mark.parametrize(
    "result",
    [
        ModelQualityGateResult(
            correlation_id=uuid4(),
            passed=passed,
            fail_category=category,
            quality_score=score,
            score_source=source,
        )
        for passed, category, score, source in (
            (True, "pass", 0.733, SCORE_SOURCE_DETERMINISTIC_ACCEPTANCE),
            (True, "pass", 0.85, SCORE_SOURCE_DETERMINISTIC_ACCEPTANCE),
            (True, "pass", 0.9, SCORE_SOURCE_COMBINED),
            (True, "pass", 0.64, SCORE_SOURCE_COMBINED),
            (False, "fail_deterministic", 0.5, SCORE_SOURCE_DETERMINISTIC_ACCEPTANCE),
            (False, "fail_heuristic", 0.9, SCORE_SOURCE_DETERMINISTIC_ACCEPTANCE),
        )
    ],
    ids=lambda result: (
        f"{result.score_source}-{result.fail_category}-{result.quality_score}"
    ),
)
def test_bus_and_local_paths_give_the_same_verdict(
    monkeypatch: pytest.MonkeyPatch, result: ModelQualityGateResult
) -> None:
    _no_escalation(monkeypatch)
    handler, intent = _bus_workflow(task_type="code_generation", content=_STUB_BODY)
    result = result.model_copy(update={"correlation_id": intent.payload.correlation_id})
    port = LocalDelegationDispatchPort.__new__(LocalDelegationDispatchPort)
    local = port._is_quality_accepted("code_generation", result)
    bus = any(
        isinstance(event, ModelDelegationCompleted)
        for event in handler.handle_gate_result(result)
    )
    bar = resolve_required_bar_authority(task_type="code_generation").required_bar
    expected = (
        result.passed
        and result.fail_category != "fail_deterministic"
        and result.quality_score >= bar
    )
    assert local == bus
    assert bus == expected
