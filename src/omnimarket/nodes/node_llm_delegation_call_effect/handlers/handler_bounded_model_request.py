# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Public single-attempt transport for already bounded OpenAI wire requests."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx
from omnibase_spi.protocols.services import ProtocolSecretStore

from omnimarket.inference.secret_store_resolver import resolve_api_key_loop_safe
from omnimarket.routing.delegation_backend_resolution import (
    ModelResolvedDelegationBackend,
)


class BoundedModelTransportError(ValueError):
    """A single upstream attempt failed or returned a non-success status."""


class BoundedModelResponseError(ValueError):
    """The upstream response exceeded its byte bound or was not a JSON object."""


def _refuse_credential_echo(value: dict[str, Any], credential: str) -> None:
    """Inspect decoded leaves so nested JSON cannot hide an escaped key."""
    needles = {
        credential,
        json.dumps(credential, ensure_ascii=False)[1:-1],
        json.dumps(credential, ensure_ascii=True)[1:-1],
    }
    pending: list[object] = [value, json.dumps(value, ensure_ascii=False)]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if any(needle in item for needle in needles):
                raise BoundedModelResponseError(
                    "model response contains platform credential material"
                )
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


class HandlerBoundedModelRequest:
    """POST exact reviewed wire bytes once through the sanctioned effect.

    The composing gateway owns payload authorization and spend reservation.
    This seam never probes, retries, changes prompts or resolves another model.
    Keys resolve from the supplied platform store by reference, never from the
    process environment. Transport errors intentionally carry no provider body.
    """

    def __init__(
        self,
        backend: ModelResolvedDelegationBackend,
        *,
        secret_store: ProtocolSecretStore,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        endpoint = urlsplit(backend.endpoint_ref)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or not endpoint.path.endswith("/chat/completions")
            or not backend.secret_ref
            or any(k.lower() == "authorization" for k in backend.extra_headers)
        ):
            raise ValueError(
                "bounded delegation requires a resolved endpoint and secret reference"
            )
        self._backend = backend
        self._store = secret_store
        self._transport = transport

    def handle(self, body: dict[str, Any]) -> dict[str, Any]:
        """Execute one bounded request; callers must reserve each invocation."""
        maximum = body.get("max_tokens")
        if (
            body.get("model") != self._backend.model_id
            or body.get("stream") is not False
            or type(maximum) is not int
            or not 1 <= maximum <= self._backend.max_tokens
        ):
            raise ValueError("model request exceeds the resolved delegation bounds")
        encoded = json.dumps(body, allow_nan=False).encode("utf-8")
        if len(encoded) > 1_048_576:
            raise ValueError("model request exceeds the wire byte limit")
        key = resolve_api_key_loop_safe(
            self._backend.secret_ref, store=self._store, required=True
        )
        if key is None:
            raise ValueError("model request has no platform credential")
        headers = {
            **self._backend.extra_headers,
            "content-type": "application/json",
            "authorization": f"Bearer {key.get_secret_value()}",
        }
        try:
            with (
                httpx.Client(
                    transport=self._transport,
                    timeout=self._backend.timeout_ms / 1000,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST", self._backend.endpoint_ref, headers=headers, content=encoded
                ) as response,
            ):
                if response.status_code != 200:
                    raise BoundedModelTransportError(
                        "bounded model request was refused"
                    )
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 2_097_152:
                        raise BoundedModelResponseError(
                            "model response exceeds the wire byte limit"
                        )
                    chunks.append(chunk)
                result = json.loads(b"".join(chunks))
                if not isinstance(result, dict):
                    raise BoundedModelResponseError("model response must be an object")
                _refuse_credential_echo(result, key.get_secret_value())
                return result
        except json.JSONDecodeError as exc:
            raise BoundedModelResponseError("model response is not valid JSON") from exc
        except httpx.HTTPError as exc:
            raise BoundedModelTransportError("bounded model request failed") from exc


__all__ = [
    "BoundedModelResponseError",
    "BoundedModelTransportError",
    "HandlerBoundedModelRequest",
]
