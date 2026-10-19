# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-20607: a developer's own keys are the tier after the declared ladder.

Measured on a developer machine with a declared local model and a Gemini and an
OpenRouter key added through ``onex models`` (both key tests passed): an
unpinned ``planning`` delegation whose local answer was refused ended FAILED
with ``no_higher_tier_available: ... tier_order=['local', 'cheap_cloud'] ...
cheap_cloud(no_routable_backend_for_task)``, while pinning either key answered.

The class's declared ``tier_order`` names platform tiers only, and the one
``cheap_cloud`` rung that serves ``planning`` is ``explicit_pin_only``, so the
tier is unroutable unpinned and its house rung is never substituted with the
developer's key. The next-key hop only ran from a route that was already one of
the developer's keys, never from the local rung.

The ladder is the shipped one, the local model is declared through a bifrost
overlay, the keys are stored the way ``onex models add`` stores them, and only
the provider call is faked. No house credential is set anywhere, so a pass
cannot come from one.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from omnimarket.enums.enum_delegation_failure_class import (
    EnumDelegationFailureClass,
)
from omnimarket.inference.local_byok_credential_adapter import (
    registered_local_byok_providers_by_registration,
)
from omnimarket.nodes.node_delegate_skill_orchestrator.ports.port_local_delegation_dispatch import (
    LocalDelegationDispatchPort,
)
from omnimarket.nodes.node_llm_delegation_call_effect import (
    ModelLlmDelegationCallRequest,
    ModelLlmDelegationCallResult,
)
from omnimarket.nodes.node_local_secret_store_effect.handlers.handler_local_secret_store import (
    HandlerLocalSecretStore,
)
from omnimarket.nodes.node_local_secret_store_effect.models.model_local_secret_request import (
    ModelLocalSecretRequest,
)
from omnimarket.projection.tenant_isolation import HOUSE_TENANT_SLUG
from omnimarket.routing.byok_provider_backends import resolve_byok_backend_by_id
from tests.chains.local.harness import use_local_store

pytestmark = pytest.mark.unit

OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
GEMINI_MODEL = "gemini-3.5-flash-lite"
LOCAL_URL = "http://127.0.0.1:18999/v1/chat/completions"


def _url(backend_id: str) -> str:
    row = resolve_byok_backend_by_id(backend_id)
    assert row is not None, backend_id
    return row.endpoint_url


OPENROUTER_URL = _url("byok-openrouter")
GEMINI_URL = _url("byok-gemini")
_PLAN = (
    "### ANSWER\n"
    "1. Inventory the service's dependencies and their owners.\n"
    "2. Sequence the work into milestones: schema first, then the readers, "
    "then the writers, because each milestone depends on the one before it.\n"
    "3. Prioritize the backlog by risk, and list the next steps for the team."
)
_PROMPT = (
    "Plan the next steps to migrate our billing service to the new schema: "
    "sequence the work into milestones, prioritize the backlog for the team "
    "over the next quarter, and cover the dependencies and risks in order."
)


class _Providers:
    """The local model fails as told; every customer key answers with a plan."""

    def __init__(self, local: str) -> None:
        self.local = local
        self.calls: list[str] = []

    def __call__(
        self, request: ModelLlmDelegationCallRequest
    ) -> ModelLlmDelegationCallResult:
        self.calls.append(request.endpoint_ref)
        common = {
            "request_id": request.request_id,
            "latency_ms": 5,
            "actual_cost_usd": Decimal("0"),
            "savings_usd": Decimal("0"),
        }
        if request.endpoint_ref == LOCAL_URL and self.local == "unreachable":
            return ModelLlmDelegationCallResult(
                success=False,
                content="",
                error_message=f"endpoint {LOCAL_URL} failed health probe",
                failure_class=EnumDelegationFailureClass.MODEL_UNAVAILABLE,
                **common,
            )
        content = "" if request.endpoint_ref == LOCAL_URL else _PLAN
        return ModelLlmDelegationCallResult(
            success=True, content=content, tokens_in=11, tokens_out=22, **common
        )


def _add_key(provider: str, model: str) -> None:
    HandlerLocalSecretStore().handle(
        ModelLocalSecretRequest(
            operation="set",
            secret_ref=f"llm.{provider}.api_key",
            value=SecretStr(f"test-{provider}-key-not-a-real-one"),
            model=model,
            force=True,
        )
    )


@pytest.fixture
def local_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A developer machine: a declared local model, no house credential."""
    for name in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "LLM_GLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    db_path = use_local_store(monkeypatch, tmp_path)
    overlay = tmp_path / "bifrost_overlay.yaml"
    overlay.write_text(
        "backends:\n"
        "  - backend_id: local-heavy-reasoning\n"
        f"    endpoint_url: {LOCAL_URL}\n"
        "    model_name: my-local-model\n"
    )
    monkeypatch.setenv("BIFROST_OVERLAY_PATH", str(overlay))
    return db_path


def _dispatch(
    tmp_path: Path, providers: _Providers, *, tenant_id: str = "acme"
) -> dict[str, object]:
    port = LocalDelegationDispatchPort(
        effect_handler=providers,
        evidence_db_path=tmp_path / "evidence.sqlite",
        effect_process_boundary=False,
    )
    return asyncio.run(
        port.dispatch(
            prompt=_PROMPT,
            task_type="planning",
            correlation_id=uuid4(),
            max_tokens=256,
            source_file_path=None,
            source_session_id=None,
            wait=True,
            execution_timeout_seconds=240,
            terminal_delivery_margin_seconds=60,
            quality_contract_mode="extend_task_class",
            acceptance_criteria=(),
            tenant_id=tenant_id,
        )
    )


def _backends(result: dict[str, object]) -> list[object]:
    attempts = result["attempts"]
    assert isinstance(attempts, list)
    return [attempt["backend_id"] for attempt in attempts]


def test_providers_are_listed_in_the_order_they_were_registered(
    local_model: Path,
) -> None:
    _add_key("openrouter", OPENROUTER_MODEL)
    _add_key("gemini", GEMINI_MODEL)
    assert registered_local_byok_providers_by_registration(db_path=local_model) == (
        "openrouter",
        "gemini",
    )


def test_a_refused_local_plan_escalates_to_the_first_registered_key(
    local_model: Path, tmp_path: Path
) -> None:
    _add_key("gemini", GEMINI_MODEL)
    _add_key("openrouter", OPENROUTER_MODEL)
    providers = _Providers(local="empty")
    result = _dispatch(tmp_path, providers)

    assert result["status"] == "completed", result
    backends = _backends(result)
    assert backends[0] == "local-heavy-reasoning"
    assert backends[-1] == "byok-gemini"
    assert "byok-openrouter" not in backends
    assert providers.calls[-1] == GEMINI_URL
    assert result["escalation_count"] == 1


def test_the_byok_tier_follows_registration_order_not_the_catalogue(
    local_model: Path, tmp_path: Path
) -> None:
    _add_key("openrouter", OPENROUTER_MODEL)
    _add_key("gemini", GEMINI_MODEL)
    providers = _Providers(local="empty")
    result = _dispatch(tmp_path, providers)

    assert result["status"] == "completed", result
    assert _backends(result)[-1] == "byok-openrouter"
    assert GEMINI_URL not in providers.calls


def test_an_unreachable_local_model_escalates_to_a_registered_key(
    local_model: Path, tmp_path: Path
) -> None:
    _add_key("gemini", GEMINI_MODEL)
    providers = _Providers(local="unreachable")
    result = _dispatch(tmp_path, providers)

    assert result["status"] == "completed", result
    assert _backends(result) == ["local-heavy-reasoning", "byok-gemini"]


def test_with_no_registered_key_the_refusal_stays_no_higher_tier_available(
    local_model: Path, tmp_path: Path
) -> None:
    providers = _Providers(local="empty")
    result = _dispatch(tmp_path, providers)

    assert result["status"] == "failed", result
    assert set(_backends(result)) == {"local-heavy-reasoning"}
    reason = str(result["terminal_failure_reason"])
    assert reason.startswith("no_higher_tier_available: "), reason
    assert "tier_order=['local', 'cheap_cloud']" in reason, reason
    assert "cheap_cloud(no_routable_backend_for_task)" in reason, reason
    assert not {GEMINI_URL, OPENROUTER_URL} & set(providers.calls)


def test_house_work_never_escalates_to_a_key_registered_on_the_machine(
    local_model: Path, tmp_path: Path
) -> None:
    _add_key("gemini", GEMINI_MODEL)
    providers = _Providers(local="empty")
    result = _dispatch(tmp_path, providers, tenant_id=HOUSE_TENANT_SLUG)

    assert result["status"] == "failed", result
    assert GEMINI_URL not in providers.calls
    assert str(result["terminal_failure_reason"]).startswith(
        "no_higher_tier_available: "
    )
