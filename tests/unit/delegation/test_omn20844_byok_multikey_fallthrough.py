# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-20844: a customer holding two keys reaches the second when the first fails.

A customer-shaped profile: a fresh HOME (no host overlay), no provider env,
and two keys registered through the onboarding handler (``onex secret set
llm.<provider>.api_key``), Gemini and OpenRouter. The real local chain runs;
only the provider's HTTP POST is faked.

Before the fix, the ladder's sibling and up-tier probes judged every platform
rung unroutable for this customer (Gemini's rungs are pin-only, GLM's need a
GLM key), so a retryable failure or a quality-gate FAIL on OpenRouter ended
the run while the Gemini key sat unused. The negative controls pin what must
not change: an auth failure still terminates, and a customer with one key is
never answered by anything else.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from omnimarket.models.delegation.wire.model_delegate_skill_response import (
    ModelDelegateSkillResponse,
)
from omnimarket.nodes.node_llm_delegation_call_effect.handlers import transport
from omnimarket.nodes.node_local_secret_store_effect.handlers.handler_local_secret_store import (
    HandlerLocalSecretStore,
)
from omnimarket.nodes.node_local_secret_store_effect.models.model_local_secret_request import (
    ModelLocalSecretRequest,
)
from omnimarket.tenant_credential_ref import is_tenant_credential_ref
from tests.chains.local.harness import run_local_delegation, use_local_store

pytestmark = pytest.mark.unit

GEMINI = "generativelanguage.googleapis.com"
OPENROUTER = "openrouter.ai"
TASK_TYPE = "document"
PASSING = "### ANSWER\nParis is the capital of France.\n"
#: No deliverable marker, so the deterministic gate refuses it.
FAILING = "Paris, probably."

_PROVIDER_ENV_MARKERS = (
    "API_KEY",
    "LLM_",
    "GEMINI",
    "OPENROUTER",
    "GLM",
    "ONEX_TENANT",
)


class FakeProviders:
    """Answers each POST by host: an HTTP status to raise, or a content string."""

    def __init__(self, script: dict[str, int | str]) -> None:
        self.script = script
        self.hosts: list[str] = []

    def post(
        self, *, endpoint_url: str, payload: dict[str, Any], **_: Any
    ) -> transport.ModelTransportResponse:
        host = str(httpx.URL(endpoint_url).host)
        self.hosts.append(host)
        step = self.script[host]  # A host outside the script fails the test.
        if isinstance(step, int):
            httpx.Response(
                step,
                request=httpx.Request("POST", endpoint_url),
                json={"error": {"message": "free-tier quota exhausted"}},
            ).raise_for_status()
        body = {
            "choices": [{"message": {"content": step}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        }
        return transport.ModelTransportResponse(
            status_code=200, json_body=body, latency_ms=1
        )


def _customer_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, providers: tuple[str, ...]
) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in list(os.environ):
        if any(marker in name.upper() for marker in _PROVIDER_ENV_MARKERS):
            monkeypatch.delenv(name, raising=False)
    db_path = use_local_store(monkeypatch, tmp_path)
    models = {"gemini": "gemini-2.5-flash", "openrouter": "google/gemma-4-31b-it:free"}
    onboarding = HandlerLocalSecretStore()
    for provider in providers:
        onboarding.handle(
            ModelLocalSecretRequest(
                operation="set",
                secret_ref=f"llm.{provider}.api_key",
                value=SecretStr(f"synthetic-{provider}-key-omn20844"),
                model=models[provider],
            )
        )
    return db_path


def _run(
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    script: dict[str, int | str],
) -> tuple[ModelDelegateSkillResponse, FakeProviders, list[dict[str, Any]]]:
    providers = FakeProviders(script)
    monkeypatch.setattr(transport, "post_chat_completion", providers.post)
    result = asyncio.run(
        run_local_delegation(
            prompt="What is the capital of France? Answer in one sentence.",
            db_path=db_path,
            correlation_id=uuid4(),
            task_type=TASK_TYPE,
            backend_id=None,
        )
    )
    attempts = list(result.model_dump(mode="json").get("attempts") or [])
    return result, providers, attempts


def test_case_a_the_first_key_answers_when_healthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ladder's house OpenRouter rung is substituted first; Gemini is unasked."""
    db_path = _customer_profile(monkeypatch, tmp_path, ("gemini", "openrouter"))

    result, providers, attempts = _run(
        monkeypatch, db_path, {OPENROUTER: PASSING, GEMINI: PASSING}
    )

    assert result.status == "completed"
    assert providers.hosts == [OPENROUTER]
    assert [a["backend_id"] for a in attempts] == ["byok-openrouter"]


def test_case_b_a_retryable_failure_falls_through_to_the_second_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = _customer_profile(monkeypatch, tmp_path, ("gemini", "openrouter"))

    result, providers, attempts = _run(
        monkeypatch, db_path, {OPENROUTER: 429, GEMINI: PASSING}
    )

    assert result.status == "completed"
    # One call plus the catalogue's two same-route retries, then the other key.
    assert providers.hosts == [OPENROUTER, OPENROUTER, OPENROUTER, GEMINI]
    assert [(a["backend_id"], a["failure_class"]) for a in attempts] == [
        ("byok-openrouter", "rate_limited"),
        ("byok-openrouter", "rate_limited"),
        ("byok-openrouter", "rate_limited"),
        ("byok-gemini", None),
    ]
    assert result.secret_ref is not None
    assert is_tenant_credential_ref(result.secret_ref)


def test_case_c_a_quality_gate_fail_moves_to_the_second_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = _customer_profile(monkeypatch, tmp_path, ("gemini", "openrouter"))

    result, providers, attempts = _run(
        monkeypatch, db_path, {OPENROUTER: FAILING, GEMINI: PASSING}
    )

    assert result.status == "completed"
    assert providers.hosts[-1] == GEMINI
    assert set(providers.hosts[:-1]) == {OPENROUTER}
    assert attempts[-1]["backend_id"] == "byok-gemini"
    assert attempts[-1]["quality_gate_passed"] is True
    assert all(a["quality_gate_passed"] is False for a in attempts[:-1])


def test_a_billing_refusal_on_the_first_key_moves_to_the_second(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A typed account refusal is about the key that failed, not the other one."""
    db_path = _customer_profile(monkeypatch, tmp_path, ("gemini", "openrouter"))

    result, providers, attempts = _run(
        monkeypatch, db_path, {OPENROUTER: 402, GEMINI: PASSING}
    )

    assert result.status == "completed"
    assert providers.hosts == [OPENROUTER, GEMINI]
    assert [(a["backend_id"], a["failure_class"]) for a in attempts] == [
        ("byok-openrouter", "provider_billing"),
        ("byok-gemini", None),
    ]


def test_an_auth_failure_still_terminates_on_the_first_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = _customer_profile(monkeypatch, tmp_path, ("gemini", "openrouter"))

    result, providers, attempts = _run(
        monkeypatch, db_path, {OPENROUTER: 401, GEMINI: PASSING}
    )

    assert result.status == "failed"
    assert providers.hosts == [OPENROUTER]
    assert [a["failure_class"] for a in attempts] == ["provider_auth_failed"]


def test_one_key_ends_the_run_and_nothing_else_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = _customer_profile(monkeypatch, tmp_path, ("openrouter",))

    result, providers, _ = _run(monkeypatch, db_path, {OPENROUTER: 429})

    assert result.status == "failed"
    assert providers.hosts == [OPENROUTER, OPENROUTER, OPENROUTER]
