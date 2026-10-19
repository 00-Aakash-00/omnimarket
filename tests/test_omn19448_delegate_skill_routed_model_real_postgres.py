# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19448: a delegate-skill terminal's accepting attempt names delegation_events.routed_model on real Postgres.

Real-Postgres twin of tests/unit/delegation/test_delegate_skill_routed_model_omn19448.py,
using the live migration set (0052 declares routed_model TEXT) and
DelegationProjectionRunner.project_event(). The module skips without a
configured, reachable INTEGRATION_POSTGRES_* server.
"""

from __future__ import annotations

import pytest

from omnimarket.projection.runner import MessageMeta
from tests.test_omn15909_real_postgres_projection_write_path_gate import (
    _provisioned_runner,
)
from tests.test_omn18928_terminal_outcome_projection_real_postgres import (
    _TENANT,
)
from tests.unit.projection.test_delegation_backend_id_and_host_omn20162 import (
    _attempt,
    _skill_terminal,
)

_SELECT = "SELECT routed_model, answering_backend FROM delegation_events WHERE correlation_id = $1"


@pytest.mark.integration
@pytest.mark.parametrize(
    "topic_name", ["_topic_delegate_skill_completed", "_topic_delegate_skill_failed"]
)
async def test_skill_terminal_stores_routed_model_and_keeps_it_across_a_sparse_terminal(
    topic_name: str,
) -> None:
    payload = _skill_terminal(
        [_attempt(backend_id="local-b", host="h202", passed=True)]
    )
    payload["tenant_id"] = _TENANT
    cid = str(payload["correlation_id"])
    async with _provisioned_runner() as (runner, admin_conn, _schema):
        topic = getattr(runner, topic_name)
        assert await runner.project_event(
            topic, payload, MessageMeta(partition=0, offset=0, fallback_id=cid)
        )
        row = await admin_conn.fetchrow(_SELECT, cid)
        assert row is not None
        assert row["routed_model"] == "qwen3.8-27b"
        assert row["answering_backend"] is None
        payload["attempts"] = []
        assert await runner.project_event(
            topic, payload, MessageMeta(partition=0, offset=1, fallback_id=cid)
        )
        assert (await admin_conn.fetchrow(_SELECT, cid))[
            "routed_model"
        ] == "qwen3.8-27b"


@pytest.mark.integration
async def test_skill_terminal_with_a_refused_ladder_stores_null_routed_model() -> None:
    payload = _skill_terminal(
        [
            _attempt(
                backend_id="cloud-a",
                host=None,
                passed=False,
                failure_class="model_unavailable",
            )
        ]
    )
    payload["tenant_id"] = _TENANT
    cid = str(payload["correlation_id"])
    async with _provisioned_runner() as (runner, admin_conn, _schema):
        assert await runner.project_event(
            runner._topic_delegate_skill_completed,
            payload,
            MessageMeta(partition=0, offset=0, fallback_id=cid),
        )
        row = await admin_conn.fetchrow(_SELECT, cid)
        assert row is not None
        assert row["routed_model"] is None
