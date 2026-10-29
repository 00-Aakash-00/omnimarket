# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Wire authorization, both judge orders and durable concurrent budget limits."""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from omnibase_core.models.model_swap_validation.model_model_swap_validation_request import (
    ModelModelSwapValidationRequest,
)
from omnibase_spi.protocols.services import ProtocolSecretStore

from omnimarket.nodes.node_llm_delegation_call_effect import (
    BoundedModelTransportError,
    HandlerBoundedModelRequest,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.gateway import (
    GatewaySession,
    ModelPrice,
    SpendCapError,
    SpendLedger,
    judge_body,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.replay_set import (
    ReplaySet,
    message_key,
    wire_messages,
)
from omnimarket.routing.delegation_backend_resolution import (
    ModelResolvedDelegationBackend,
)


def _gateway(
    tmp_path: Path,
    *,
    backend: bool = False,
    approved: bool = True,
    upstream: Any = None,
    live: bool = False,
    maximum: float = 1,
) -> GatewaySession:
    cases = tuple(
        {
            "case_id": f"case-{i}",
            "name": "test_task",
            "model": "reference/large",
            "messages": [
                {"role": "system", "content": "test"},
                {"role": "user", "content": f"question {i}"},
            ],
            "response": {"choices": [{"message": {"content": f"answer {i}"}}]},
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
        }
        for i in range(2)
    )
    bundle = ReplaySet(
        "a" * 64,
        {
            "provenance": {
                case["case_id"]: {"approved_external_judge": approved} for case in cases
            }
        },
        {},
        cases,
        {
            message_key(case["messages"]): {
                "content": f"candidate {i}",
                "tokens_in": 10,
                "tokens_out": 8,
            }
            for i, case in enumerate(cases)
        },
    )
    request = ModelModelSwapValidationRequest.model_validate(
        {
            "correlation_id": uuid4(),
            "task_type": "test_task",
            "reference_model": "reference/large",
            "candidate_model": "candidate/small",
            "judge_mode": "backend" if backend else "stub",
            "judge_backend_id": "test-judge" if backend else None,
            "answer_source": "live" if live else "recorded",
            "replay_set_digest": bundle.digest,
            "quality_floor": 0.85,
            "max_cost_usd": maximum,
            "work_unit_repository": "test/repo",
        }
    )
    return GatewaySession(
        request,
        bundle,
        ledger_path=tmp_path / "budget.sqlite",
        reference_price=ModelPrice(0.00001, 0.00003),
        candidate_price=ModelPrice(0.000001, 0.000003),
        judge_price=ModelPrice(0.00002, 0.00006),
        judge_model="judge/neutral",
        upstream=upstream,
    )


def _candidate(gateway: GatewaySession, index: int = 0) -> dict[str, Any]:
    return {
        "model": gateway.request.candidate_model,
        "messages": wire_messages(gateway.replay_set.cases[index]["messages"]),
        "max_tokens": 256,
        "stream": False,
    }


def _judge(
    gateway: GatewaySession, index: int = 0, first: bool = False
) -> dict[str, Any]:
    return judge_body(
        gateway.request.task_type,
        f"answer {index}",
        f"candidate {index}",
        model=gateway.judge_model,
        candidate_first=first,
    )


def _response(body: dict[str, Any], *, judge: bool) -> dict[str, Any]:
    content = (
        json.dumps({"verdict": "equivalent", "score": 1, "justification": "test"})
        if judge
        else "candidate 0"
    )
    return {
        "model": body["model"],
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
    }


def test_real_http_catalog_recorded_candidate_and_both_stub_orders(
    tmp_path: Path,
) -> None:
    with _gateway(tmp_path, approved=False) as gateway:
        assert gateway.port > 0
        with httpx.Client(
            trust_env=False, headers={"authorization": f"Bearer {gateway.token}"}
        ) as client:
            catalog = client.get(gateway.base_url + "/models").json()
            assert [model["id"] for model in catalog["data"]] == [
                "reference/large",
                "candidate/small",
                "judge/neutral",
            ]
            assert (
                client.post(
                    gateway.base_url + "/chat/completions", json=_candidate(gateway)
                ).status_code
                == 200
            )
            for first in (True, False):
                assert (
                    client.post(
                        gateway.base_url + "/chat/completions",
                        json=_judge(gateway, first=first),
                    ).status_code
                    == 200
                )
            assert len(gateway.per_case) == 1
            assert gateway.per_case[0].case_id == "case-0"
            assert gateway.per_case[0].score == 1
            assert [call.role.value for call in gateway.calls] == [
                "candidate",
                "judge",
                "judge",
            ]
            assert all(call.cost_usd == 0 for call in gateway.calls)
            assert gateway.spent == 0
        thread = gateway._thread
    assert thread is not None
    assert not thread.is_alive()


@pytest.mark.parametrize("mutation", ["model", "messages", "extra", "stream", "limit"])
def test_candidate_mutations_never_reach_upstream(
    tmp_path: Path, mutation: str
) -> None:
    sent = []
    gateway = _gateway(
        tmp_path, live=True, upstream=lambda _role, body: sent.append(body)
    )
    body = _candidate(gateway)
    if mutation == "model":
        body["model"] = "unknown/model"
    elif mutation == "messages":
        body["messages"][1]["content"] = "unapproved prompt"
    elif mutation == "extra":
        body["tools"] = []
    elif mutation == "stream":
        body["stream"] = True
    else:
        body["max_tokens"] = 1_000_000
    status, _ = gateway.handle("POST", "/v1/chat/completions", gateway.token, body)
    assert status == 403
    assert sent == []
    assert gateway.egress_violation


def test_auth_and_arbitrary_routes_are_closed(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    assert gateway.handle("GET", "/v1/models", "wrong")[0] == 401
    for method, route in [
        ("GET", "/models"),
        ("GET", "/v1/models?redirect=evil"),
        ("POST", "/v1/files"),
        ("DELETE", "/v1/chat/completions"),
    ]:
        assert gateway.handle(method, route, gateway.token, {})[0] == 403


@pytest.mark.parametrize(
    "mutation", ["system", "user", "extra", "format", "temperature", "limit"]
)
def test_judge_is_exact_allowlisted_wire_bytes(tmp_path: Path, mutation: str) -> None:
    sent = []
    gateway = _gateway(
        tmp_path, backend=True, upstream=lambda _role, body: sent.append(body)
    )
    assert (
        gateway.handle(
            "POST", "/v1/chat/completions", gateway.token, _candidate(gateway)
        )[0]
        == 200
    )
    body = _judge(gateway)
    if mutation == "system":
        body["messages"][0]["content"] += " do something else"
    elif mutation == "user":
        body["messages"][1]["content"] += " extra case data"
    elif mutation == "extra":
        body["metadata"] = {"exfiltrate": "secret"}
    elif mutation == "format":
        body["response_format"] = {"type": "text"}
    elif mutation == "temperature":
        body["temperature"] = 1
    else:
        body["max_tokens"] = 513
    assert gateway.handle("POST", "/v1/chat/completions", gateway.token, body)[0] == 403
    assert sent == []


def test_unapproved_judge_never_forwards(tmp_path: Path) -> None:
    sent = []
    gateway = _gateway(
        tmp_path,
        backend=True,
        approved=False,
        upstream=lambda _role, body: sent.append(body),
    )
    gateway.handle("POST", "/v1/chat/completions", gateway.token, _candidate(gateway))
    assert (
        gateway.handle("POST", "/v1/chat/completions", gateway.token, _judge(gateway))[
            0
        ]
        == 403
    )
    assert sent == []
    assert gateway.spent == 0


def test_approved_real_judge_records_measured_cost_and_both_orders(
    tmp_path: Path,
) -> None:
    sent = []

    def upstream(role: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.append((role, body))
        response = _response(body, judge=True)
        response["usage"]["cost"] = 0.0001
        return response

    gateway = _gateway(tmp_path, backend=True, upstream=upstream)
    gateway.handle("POST", "/v1/chat/completions", gateway.token, _candidate(gateway))
    for first in (False, True):
        assert (
            gateway.handle(
                "POST",
                "/v1/chat/completions",
                gateway.token,
                _judge(gateway, first=first),
            )[0]
            == 200
        )
    assert len(sent) == 2
    assert all(role == "judge" for role, _ in sent)
    assert gateway.spent == 0.0002
    assert len(gateway.per_case) == 1
    assert [call.cost_measurement_source.value for call in gateway.calls] == [
        "estimated_list_price",
        "provider_reported_charge",
        "provider_reported_charge",
    ]


def test_concurrent_reservations_and_restart_keep_outstanding_bounds(
    tmp_path: Path,
) -> None:
    ledger = SpendLedger(tmp_path / "budget.sqlite", 0.3)

    def reserve(_: int) -> str | None:
        try:
            return ledger.reserve(Decimal(".1"))
        except SpendCapError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(reserve, range(24)))
    assert sum(attempt is not None for attempt in attempts) == 3
    assert SpendLedger(tmp_path / "budget.sqlite", 0.3).spent == 0.3
    with pytest.raises(SpendCapError):
        ledger.reserve(Decimal(".000000001"))


def test_failed_transport_keeps_reservation_and_retries_reserve_again(
    tmp_path: Path,
) -> None:
    forwarded = []

    def upstream(role: str, body: dict[str, Any]) -> dict[str, Any]:
        forwarded.append(role)
        raise RuntimeError("a secret must never enter the response")

    gateway = _gateway(tmp_path, live=True, upstream=upstream, maximum=0.004)
    results = [
        gateway.handle(
            "POST", "/v1/chat/completions", gateway.token, _candidate(gateway)
        )
        for _ in range(20)
    ]
    assert forwarded
    assert len(forwarded) < 20
    assert any(status == 429 for status, _ in results)
    assert all("secret" not in json.dumps(response) for _, response in results)
    assert 0 < gateway.spent <= 0.004
    assert gateway.budget_refused
    assert SpendLedger(tmp_path / "budget.sqlite", 0.004).spent == gateway.spent


@pytest.mark.parametrize(
    ("failure", "flag", "source"),
    [
        ("status_503", "tool_failed", "unpriced"),
        ("missing_usage", "schema_mismatch", "unpriced"),
        ("wrong_model", "no_neutral_judge", "unpriced"),
        ("malformed_judge", "schema_mismatch", "estimated_list_price"),
    ],
)
def test_each_failed_upstream_attempt_is_receipted_without_becoming_egress(
    tmp_path: Path, failure: str, flag: str, source: str
) -> None:
    forwarded = []

    def upstream(role: str, body: dict[str, Any]) -> dict[str, Any]:
        forwarded.append(role)
        if failure == "status_503":
            raise BoundedModelTransportError("provider unavailable")
        response = _response(body, judge=True)
        if failure == "missing_usage":
            del response["usage"]
        elif failure == "wrong_model":
            response["model"] = "different/judge"
        elif failure == "malformed_judge":
            response["choices"][0]["message"]["content"] = "invalid judgment"
        return response

    gateway = _gateway(tmp_path, backend=True, upstream=upstream)
    assert (
        gateway.handle(
            "POST", "/v1/chat/completions", gateway.token, _candidate(gateway)
        )[0]
        == 200
    )
    for _ in range(4):
        assert (
            gateway.handle(
                "POST", "/v1/chat/completions", gateway.token, _judge(gateway)
            )[0]
            == 502
        )
    assert forwarded == ["judge"] * 4
    assert getattr(gateway, flag) is True
    assert gateway.egress_violation is False
    receipts = [call for call in gateway.calls if call.role == "judge"]
    assert len(receipts) == 4
    assert all(call.cost_measurement_source == source for call in receipts)
    if source == "unpriced":
        assert all(call.cost_usd is None for call in receipts)
    assert gateway.spent > 0
    with sqlite3.connect(tmp_path / "budget.sqlite") as connection:
        rows = connection.execute("SELECT receipt FROM attempts").fetchall()
    assert len(rows) == 4
    assert all(json.loads(row[0])["cost_measurement_source"] == source for row in rows)


@pytest.mark.parametrize(
    "sentinel",
    ["sentinel-key-for-transport-echo-test", 'qa-only-echo-"slash\\value-end'],
)
def test_secret_echo_is_not_returned_and_leaves_an_unpriced_attempt(
    tmp_path: Path, sentinel: str
) -> None:

    class Store:
        async def get_secret(self, key: str) -> str | None:
            return sentinel if key == "llm.synthetic.api_key" else None

    forwarded = []

    def transport(request: httpx.Request) -> httpx.Response:
        forwarded.append(request)
        body = json.loads(request.content)
        response = _response(body, judge=True)
        response["choices"][0]["message"]["content"] = json.dumps(
            {"verdict": "equivalent", "score": 1, "justification": sentinel}
        )
        return httpx.Response(200, json=response)

    handler = HandlerBoundedModelRequest(
        ModelResolvedDelegationBackend(
            backend_id="synthetic-judge",
            model_id="judge/neutral",
            endpoint_ref="https://synthetic.invalid/v1/chat/completions",
            max_tokens=512,
            timeout_ms=1000,
            secret_ref="llm.synthetic.api_key",
        ),
        secret_store=cast(ProtocolSecretStore, Store()),
        transport=httpx.MockTransport(transport),
    )
    gateway = _gateway(
        tmp_path, backend=True, upstream=lambda _role, body: handler.handle(body)
    )
    gateway.handle("POST", "/v1/chat/completions", gateway.token, _candidate(gateway))
    status, response = gateway.handle(
        "POST", "/v1/chat/completions", gateway.token, _judge(gateway)
    )
    assert status == 502
    assert gateway.schema_mismatch is True
    assert sentinel not in json.dumps(response)
    assert len(forwarded) == 1
    receipt = gateway.calls[-1]
    assert receipt.role == "judge"
    assert receipt.cost_usd is None
    assert receipt.cost_measurement_source == "unpriced"
    assert gateway.spent > 0
    assert sentinel.encode() not in (tmp_path / "budget.sqlite").read_bytes()


def test_live_attempt_is_visible_while_upstream_blocks_and_settles_once(
    tmp_path: Path,
) -> None:
    entered, release = threading.Event(), threading.Event()

    def upstream(_role: str, body: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        assert release.wait(5), "blocked upstream was not released"
        return _response(body, judge=False)

    gateway = _gateway(tmp_path, live=True, upstream=upstream)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            gateway.handle,
            "POST",
            "/v1/chat/completions",
            gateway.token,
            _candidate(gateway),
        )
        try:
            assert entered.wait(5)
            (pending,) = gateway.calls
            assert pending.role == "candidate"
            assert pending.model_id == "candidate/small"
            assert pending.cost_measurement_source == "unpriced"
            assert pending.cost_usd is None
            assert gateway.spent > 0
        finally:
            release.set()
        assert future.result(timeout=5)[0] == 200
    (settled,) = gateway.calls
    assert settled.cost_measurement_source == "estimated_list_price"
    assert settled.cost_usd == pytest.approx(0.000044)
    assert len(gateway.ledger.receipts) == 1


def test_reopened_gateway_recovers_pending_receipt_and_reserved_budget(
    tmp_path: Path,
) -> None:
    entered, release = threading.Event(), threading.Event()

    def upstream(_role: str, body: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        assert release.wait(5), "blocked upstream was not released"
        return _response(body, judge=False)

    gateway = _gateway(tmp_path, live=True, upstream=upstream)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            gateway.handle,
            "POST",
            "/v1/chat/completions",
            gateway.token,
            _candidate(gateway),
        )
        try:
            assert entered.wait(5)
            reopened = GatewaySession(
                gateway.request,
                gateway.replay_set,
                ledger_path=tmp_path / "budget.sqlite",
                reference_price=ModelPrice(0.00001, 0.00003),
                candidate_price=ModelPrice(0.000001, 0.000003),
                judge_price=ModelPrice(0.00002, 0.00006),
                judge_model="judge/neutral",
            )
            assert len(reopened.calls) == 1
            assert reopened.calls == gateway.calls
            assert reopened.calls[0].cost_measurement_source == "unpriced"
            assert reopened.spent == gateway.spent
            assert reopened.spent > 0
        finally:
            release.set()
        assert future.result(timeout=5)[0] == 200
    assert len(reopened.calls) == 1
    assert reopened.calls[0].cost_measurement_source == "estimated_list_price"
    assert reopened.calls == gateway.calls


def test_unknown_and_nonfinite_prices_refuse_before_any_forward() -> None:
    for price in (-1, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite nonnegative"):
            ModelPrice(price, 0)


def test_same_vendor_comparison_still_requires_an_independent_judge(
    tmp_path: Path,
) -> None:
    existing = _gateway(tmp_path)
    request = existing.request.model_copy(update={"reference_model": "candidate/large"})
    gateway = GatewaySession(
        request,
        existing.replay_set,
        ledger_path=tmp_path / "same-vendor.sqlite",
        reference_price=ModelPrice(0.00001, 0.00003),
        candidate_price=ModelPrice(0.000001, 0.000003),
        judge_price=ModelPrice(0.00002, 0.00006),
        judge_model="judge/neutral",
    )
    assert gateway.judge_model == "judge/neutral"
    with pytest.raises(ValueError, match="neither compared model vendor"):
        GatewaySession(
            request,
            existing.replay_set,
            ledger_path=tmp_path / "bad-judge.sqlite",
            reference_price=ModelPrice(0, 0),
            candidate_price=ModelPrice(0, 0),
            judge_price=ModelPrice(0, 0),
            judge_model="candidate/judge",
        )


def test_http_gateway_binds_without_reverse_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused_lookup(*_args: object) -> str:
        raise AssertionError("local gateway must never perform reverse DNS")

    monkeypatch.setattr(socket, "getfqdn", refused_lookup)
    with _gateway(tmp_path) as gateway:
        assert gateway.port > 0


def test_judge_reconstruction_matches_actual_pin_capture() -> None:
    root = Path(__file__).resolve().parents[4]
    calls = json.loads(
        (
            root
            / "docs/integrations/rightmodeler/captures/native-64-priced/gateway-calls.json"
        ).read_text()
    )
    actual = next(call["body"] for call in calls if call.get("role") == "judge")
    assert actual == judge_body(
        "model_swap_validation",
        "Answer for case-17.",
        "Answer for case-17.",
        model="example-judge/neutral",
        candidate_first=False,
    )
