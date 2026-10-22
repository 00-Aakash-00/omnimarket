# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19448: a failed run's row names the model, backend, host and duration.

Measured on the dev lane over the week to 2026-10-22: of 1,630 failed
``delegation_events`` rows, 983 had a blank or ``none`` model, 1,629 had no
``backend_id`` and 1,397 read a 0 ms duration, while 72 of 20,817 passed rows
lacked a backend. The row builder named the backend, host and routed model only
for an accepted rung, so a run refused on every rung named none of them, and a
terminal whose port timed no call wrote ``latency_ms`` 0.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from omnimarket.models.delegation.wire.model_delegate_skill_request import (
    ModelDelegateSkillRequest,
)
from omnimarket.nodes.node_delegate_skill_orchestrator.handlers.handler_delegate_skill import (
    _response_from_result,
)
from omnimarket.nodes.node_projection_delegation.handlers.handler_projection_delegation import (
    TABLE,
    HandlerProjectionDelegation,
)
from omnimarket.projection.protocol_database import InmemoryDatabaseAdapter

pytestmark = pytest.mark.unit


def _refused_rung(model: str, backend: str, host: str, tier: str) -> dict[str, object]:
    return {
        "tier": tier,
        "backend_id": backend,
        "host": host,
        "model_id": model,
        "quality_gate_passed": False,
        "quality_score": 0.0,
        "cost_usd": 0.0,
        "failure_class": "quality_gate_failed",
        "error_message": "TASK_MISMATCH: failed covers_dependencies",
        "acceptance_decision": "climb",
        "acceptance_reason": "acceptance_criteria_failed",
    }


def _failed_terminal(
    attempts: list[dict[str, object]],
    *,
    latency_ms: int = 0,
    execution_duration_ms: int | None = 110142,
) -> dict[str, object]:
    return {
        "_event_type": "onex.evt.omnimarket.delegate-skill-failed.v1",
        "tenant_id": "omninode",
        "status": "failed",
        "correlation_id": str(uuid4()),
        "timestamp": "2026-10-22T14:10:44+00:00",
        "task_type": "summarization",
        "model_name": "",
        "response": "",
        "quality_gate_passed": False,
        "quality_score": 0.0,
        "terminal_failure_cause": "quality_gate_refused",
        "error_message": "every rung was refused by the quality gate",
        "metrics": {
            "input_tokens": 933,
            "output_tokens": 174,
            "latency_ms": latency_ms,
        },
        "execution_duration_ms": execution_duration_ms,
        "attempts": attempts,
    }


def _project(event: dict[str, object]) -> dict[str, Any]:
    db = InmemoryDatabaseAdapter()
    result = HandlerProjectionDelegation().handle({**event, "_db": db})
    assert result["rows_upserted"] == 1
    rows = db.query(TABLE)
    assert len(rows) == 1
    return rows[0]


def test_a_run_refused_on_every_rung_names_its_deciding_rung() -> None:
    row = _project(
        _failed_terminal(
            [
                _refused_rung(
                    "Qwen3.8-27B", "local-heavy-reasoning", "lab-host-a", "local"
                ),
                _refused_rung(
                    "cohere/north-mini-code:free",
                    "openrouter-north-mini",
                    "openrouter.ai",
                    "cheap_frontier",
                ),
            ]
        )
    )
    assert row["terminal_ok"] is False
    assert row["terminal_failure_cause"] == "quality_gate_refused"
    assert row["model_name"] == "cohere/north-mini-code:free"
    assert row["delegated_to"] == "cohere/north-mini-code:free"
    assert row["routed_model"] == "cohere/north-mini-code:free"
    assert row["backend_id"] == "openrouter-north-mini"
    assert row["host"] == "openrouter.ai"
    assert row["cost_tier_name"] == "cheap_frontier"
    # A refused rung did not deliver the run's answer.
    assert not row.get("answering_backend")


def test_a_failure_with_no_rungs_states_no_backend() -> None:
    row = _project(_failed_terminal([]))
    assert not row.get("backend_id")
    assert not row.get("host")
    assert not row.get("model_name")


def test_a_terminal_that_timed_no_call_reports_its_measured_duration() -> None:
    row = _project(_failed_terminal([], latency_ms=0, execution_duration_ms=110142))
    assert row["latency_ms"] == 110142
    assert row["delegation_latency_ms"] == 110142


def test_a_measured_latency_is_kept() -> None:
    row = _project(_failed_terminal([], latency_ms=1200, execution_duration_ms=110142))
    assert row["latency_ms"] == 1200


def _request() -> ModelDelegateSkillRequest:
    return ModelDelegateSkillRequest(
        prompt="summarize the facts below in at most 85 words",
        task_type="summarization",
        source="claude-code",
        correlation_id=uuid4(),
    )


def test_the_producer_reports_the_duration_when_the_port_names_no_latency() -> None:
    response = _response_from_result(
        _request(),
        {
            "status": "timeout",
            "content": "",
            "error_message": "timed out at stage=terminal_wait",
            "terminal_failure_cause": "timeout",
        },
        tenant_id=None,
        queue_wait_ms=None,
        execution_duration_ms=50012,
        budget_evidence=None,
    )
    assert response.metrics.latency_ms == 50012


def test_the_producer_keeps_a_latency_the_port_measured() -> None:
    response = _response_from_result(
        _request(),
        {
            "status": "completed",
            "content": "an answer",
            "quality_gate_passed": True,
            "quality_score": 1.0,
            "latency_ms": 11780,
        },
        tenant_id=None,
        queue_wait_ms=None,
        execution_duration_ms=12851,
        budget_evidence=None,
    )
    assert response.metrics.latency_ms == 11780


def _timed_out_terminal(correlation_id: str) -> dict[str, object]:
    """A delegate-skill timeout terminal: no model, no rungs, as recorded."""
    event = _failed_terminal([], execution_duration_ms=110062)
    event.update(
        {
            "status": "timeout",
            "correlation_id": correlation_id,
            "terminal_failure_cause": "timeout",
            "error_message": "timed out at stage=terminal_wait",
        }
    )
    return event


def _stored_then_timed_out(stored: dict[str, object]) -> dict[str, Any]:
    """Project a timeout terminal over a row an earlier terminal left."""
    db = InmemoryDatabaseAdapter()
    handler = HandlerProjectionDelegation()
    first = _timed_out_terminal(str(uuid4()))
    handler.handle({**first, "_db": db})
    db.tables[TABLE][0].update(stored)
    handler.handle({**first, "_db": db})
    rows = db.query(TABLE)
    assert len(rows) == 1
    return rows[0]


def test_a_sparse_timeout_terminal_keeps_the_stored_model() -> None:
    row = _stored_then_timed_out(
        {
            "model_name": "Qwen3.8-27B",
            "delegated_to": "Qwen3.8-27B",
            "routed_model": "Qwen3.8-27B",
            "backend_id": "local-coder",
        }
    )
    assert row["terminal_failure_cause"] == "timeout"
    assert row["model_name"] == "Qwen3.8-27B"
    assert row["delegated_to"] == "Qwen3.8-27B"
    assert row["backend_id"] == "local-coder"


def test_a_row_holding_a_routed_model_names_it_as_the_model() -> None:
    # Recorded on the dev lane, 2026-10-22: run c4a2b68a held routed_model
    # Qwen3.8-27B and answering_backend local-coder, model_name blank and
    # delegated_to the delegate-skill placeholder, backend_id blank.
    row = _stored_then_timed_out(
        {
            "model_name": "",
            "delegated_to": "delegate-skill",
            "routed_model": "Qwen3.8-27B",
            "answering_backend": "local-coder",
            "backend_id": None,
        }
    )
    assert row["model_name"] == "Qwen3.8-27B"
    assert row["delegated_to"] == "Qwen3.8-27B"
    assert row["backend_id"] == "local-coder"


def test_a_timeout_with_no_routing_evidence_names_no_model() -> None:
    row = _project(_timed_out_terminal(str(uuid4())))
    assert not row.get("model_name")
    assert not row.get("backend_id")
    assert row["latency_ms"] == 110062
