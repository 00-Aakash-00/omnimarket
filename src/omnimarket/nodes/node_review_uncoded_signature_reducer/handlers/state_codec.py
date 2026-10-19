# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Bridge the runtime's database state_io seam without a process-local store."""

from __future__ import annotations

import json
from contextvars import ContextVar

from omnimarket.models.model_review_finding_signature import ModelReviewUncodedState

_PENDING: ContextVar[dict[str, tuple[str | None, ModelReviewUncodedState]] | None] = (
    ContextVar(
        "review_uncoded_signature_pending",
        default=None,
    )
)


def _rows() -> dict[str, tuple[str | None, int]]:
    from omnibase_infra.runtime.state_io.state_store_adapter import (
        CONTEXTVAR_STATE_IO_ROWS,
    )

    rows = CONTEXTVAR_STATE_IO_ROWS.get()
    if rows is None:
        raise RuntimeError(
            "review uncoded reducer requires the durable state_io dispatch seam"
        )
    return rows


def load_state(key: str) -> ModelReviewUncodedState | None:
    rows = _rows()
    if key not in rows:
        raise RuntimeError("state_io did not load the finding family partition")
    raw, _version = rows[key]
    return StateIoCodec().decode(raw) if raw is not None else None


def save_state(key: str, state: ModelReviewUncodedState) -> None:
    raw, _version = _rows()[key]
    pending = dict(_PENDING.get() or {})
    pending[key] = (raw, state)
    _PENDING.set(pending)


class StateIoCodec:
    """Contract-declared codec; runtime CAS persists state and candidate together."""

    def encode(self, state: ModelReviewUncodedState) -> bytes:
        payload = state.model_dump(mode="json")
        # Each input fold is complete even when it produced no candidate. The
        # runtime refuses to seed a nonterminal workflow with an empty outbox;
        # COMPLETED describes this fold, not retirement of the family partition.
        payload.update(state="COMPLETED", in_flight=False, tenant_id="")
        return json.dumps(payload).encode()

    def decode(self, raw: bytes | str) -> ModelReviewUncodedState:
        payload = json.loads(raw)
        for key in ("state", "in_flight", "tenant_id"):
            payload.pop(key, None)
        return ModelReviewUncodedState.model_validate(payload)

    def flush(self, key: str) -> str | None:
        pending = dict(_PENDING.get() or {})
        item = pending.pop(key, None)
        _PENDING.set(pending)
        if item is None:
            return None
        source, state = item
        raw, _version = _rows()[key]
        if source is not raw:
            raise RuntimeError("state_io flush belongs to a different dispatch")
        return self.encode(state).decode()
