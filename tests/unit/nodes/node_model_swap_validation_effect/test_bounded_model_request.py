# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The sanctioned raw seam resolves the platform store and performs one POST."""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest
from omnibase_spi.protocols.services import ProtocolSecretStore

from omnimarket.nodes.node_llm_delegation_call_effect import (
    BoundedModelResponseError,
    HandlerBoundedModelRequest,
)
from omnimarket.routing.delegation_backend_resolution import (
    ModelResolvedDelegationBackend,
)


class Store:
    async def get_secret(self, key: str) -> str | None:
        return "platform-store-key" if key == "llm.synthetic.api_key" else None


def _handler(
    response: httpx.Response, sent: list[httpx.Request]
) -> HandlerBoundedModelRequest:
    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return response

    return HandlerBoundedModelRequest(
        ModelResolvedDelegationBackend(
            backend_id="test",
            model_id="test/model",
            endpoint_ref="https://synthetic.invalid/v1/chat/completions",
            max_tokens=512,
            timeout_ms=1000,
            secret_ref="llm.synthetic.api_key",
        ),
        secret_store=cast(ProtocolSecretStore, Store()),
        transport=httpx.MockTransport(transport),
    )


def _body() -> dict[str, Any]:
    return {
        "model": "test/model",
        "messages": [{"role": "user", "content": "exact text"}],
        "max_tokens": 16,
        "stream": False,
    }


def test_exact_single_post_uses_store_despite_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_SYNTHETIC_API_KEY", "wrong-environment-key")
    sent = []
    handler = _handler(httpx.Response(200, json={"model": "test/model"}), sent)
    assert handler.handle(_body()) == {"model": "test/model"}
    assert len(sent) == 1
    assert str(sent[0].url) == "https://synthetic.invalid/v1/chat/completions"
    assert json.loads(sent[0].content) == _body()
    assert sent[0].headers["authorization"] == "Bearer platform-store-key"


@pytest.mark.parametrize("status", [302, 429, 500])
def test_failure_never_retries_redirects_or_exposes_body(status: int) -> None:
    sent = []
    handler = _handler(
        httpx.Response(
            status,
            text="private provider error",
            headers={"location": "https://elsewhere.invalid"},
        ),
        sent,
    )
    with pytest.raises(ValueError, match="refused") as error:
        handler.handle(_body())
    assert len(sent) == 1
    assert "private provider error" not in str(error.value)


def test_model_and_output_limit_refuse_before_post() -> None:
    sent = []
    handler = _handler(httpx.Response(200, json={}), sent)
    for body in [
        {**_body(), "model": "other/model"},
        {**_body(), "max_tokens": 513},
        {**_body(), "stream": True},
    ]:
        with pytest.raises(ValueError, match="resolved delegation bounds"):
            handler.handle(body)
    assert sent == []


def test_provider_cannot_echo_the_platform_key_into_its_response() -> None:
    sent = []
    handler = _handler(
        httpx.Response(200, json={"content": "echo platform-store-key"}), sent
    )
    with pytest.raises(BoundedModelResponseError, match="credential material") as error:
        handler.handle(_body())
    assert len(sent) == 1
    assert "platform-store-key" not in str(error.value)
