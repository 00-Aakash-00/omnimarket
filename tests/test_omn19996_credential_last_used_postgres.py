# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19996 on real Postgres: the writers only move last_used_at forward.

last_used_at is a TIMESTAMPTZ.

Runs only against the governed CI database named by INTEGRATION_POSTGRES_*
(see tests/test_omn16316_real_postgres_tenant_credentials_write_path.py);
a skip is not proof. A mock database cannot catch a str-vs-datetime column
mismatch (OMN-15905), which is why this file exists.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from omnimarket.projection.postgres_sync_database import (
    PostgresSyncProjectionAdapter,
)
from omnimarket.projection.runner import MessageMeta
from tests.test_omn16316_real_postgres_tenant_credentials_write_path import (
    BYOK_TENANT,
    TOPIC_REGISTERED,
    _base_dsn,
    _provisioned_runner,
)

TOPIC_COMPLETED = "onex.evt.omnimarket.delegate-skill-completed.v1"
T1 = "2026-10-28T04:00:00+00:00"
T0 = "2026-10-27T04:00:00+00:00"


def _meta(topic: str, offset: int) -> MessageMeta:
    return MessageMeta(
        partition=0,
        offset=offset,
        fallback_id=f"omn19996-{offset}",
        topic=topic,
    )


async def _register(runner: Any, ref: str) -> None:
    assert await runner.project_event(
        TOPIC_REGISTERED,
        {
            "tenant_id": BYOK_TENANT,
            "provider": "openrouter",
            "name": ref,
            "api_key_ref": ref,
        },
        _meta(TOPIC_REGISTERED, 1),
    )


async def _last_used(conn: Any, ref: str) -> datetime | None:
    return await conn.fetchval(
        "SELECT last_used_at FROM tenant_inference_credentials WHERE api_key_ref = $1",
        ref,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_postgres_terminal_moves_last_used_forward_only() -> None:
    async with _provisioned_runner() as (runner, conn, _schema):
        ref = "cred_test_openrouter_last_used"
        await _register(runner, ref)
        assert await _last_used(conn, ref) is None

        for offset, at in ((2, T1), (3, T0)):
            await runner.project_event(
                TOPIC_COMPLETED,
                {
                    "correlation_id": (f"c0ffee00-0000-4000-8000-00000000000{offset}"),
                    "tenant_id": BYOK_TENANT,
                    "secret_ref": ref,
                    "_envelope_timestamp": at,
                },
                _meta(TOPIC_COMPLETED, offset),
            )

        assert await _last_used(conn, ref) == datetime.fromisoformat(T1)

        await runner.project_event(
            TOPIC_COMPLETED,
            {
                "correlation_id": "c0ffee00-0000-4000-8000-000000000004",
                "tenant_id": BYOK_TENANT,
                "secret_ref": "cred_test_openrouter_unknown",
                "_envelope_timestamp": T1,
            },
            _meta(TOPIC_COMPLETED, 4),
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM tenant_inference_credentials "
                "WHERE api_key_ref = $1",
                "cred_test_openrouter_unknown",
            )
            == 0
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_postgres_sync_adapter_moves_last_used_forward_only() -> None:
    async with _provisioned_runner() as (runner, conn, schema):
        ref = "cred_test_openrouter_sync_last_used"
        await _register(runner, ref)

        db = PostgresSyncProjectionAdapter(_base_dsn(), schema=schema)

        assert await asyncio.to_thread(db.record_credential_last_used, ref, T1) is True
        assert await _last_used(conn, ref) == datetime.fromisoformat(T1)

        assert await asyncio.to_thread(db.record_credential_last_used, ref, T0) is False
        assert await _last_used(conn, ref) == datetime.fromisoformat(T1)

        assert (
            await asyncio.to_thread(
                db.record_credential_last_used,
                "cred_test_openrouter_sync_unknown",
                T1,
            )
            is False
        )
