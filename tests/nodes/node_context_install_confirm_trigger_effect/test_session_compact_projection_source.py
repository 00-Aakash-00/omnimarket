# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Exercise the projection SQL and applied-event bridge without a live bus."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from pydantic import ValidationError

from omnimarket.models.context_install_confirm import EnumInstallFact
from omnimarket.models.context_install_confirm_trigger import (
    EnumInstallConfirmTriggerOutcome,
    ModelSessionContentProjectionAppliedEvent,
)
from omnimarket.nodes.node_context_install_confirm_compute.handlers.handler_context_install_confirm import (
    HandlerContextInstallConfirm,
)
from omnimarket.nodes.node_context_install_confirm_trigger_effect.handlers.handler_context_install_confirm_trigger import (
    HandlerContextInstallConfirmTrigger,
)
from omnimarket.nodes.node_context_install_confirm_trigger_effect.handlers.session_compact_projection_source import (
    SessionCompactProjectionSource,
)
from tests.nodes.node_context_install_confirm_request_compute.test_handler_context_install_confirm_request import (
    CAPTURED,
    SESSION,
    activation,
    chunks,
    package,
)

pytestmark = pytest.mark.unit


class Database:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("ATTACH DATABASE ':memory:' AS omninode_internal")
        self.connection.execute("""
            CREATE TABLE omninode_internal.session_content (
                event_id TEXT PRIMARY KEY, session_id TEXT, content_kind TEXT,
                chunk_index INTEGER, chunk_count INTEGER, content TEXT,
                content_sha256 TEXT, truncated INTEGER, emitted_at TEXT
            )
        """)
        self.reads = []

    def add(self, chunk, *, kind="compact"):
        self.connection.execute(
            "INSERT INTO omninode_internal.session_content VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chunk.event_id,
                chunk.session_id,
                kind,
                chunk.chunk_index,
                chunk.chunk_count,
                chunk.content,
                chunk.content_sha256,
                chunk.truncated,
                chunk.emitted_at.isoformat(),
            ),
        )

    async def execute(self, query, *params):
        self.reads.append((query, params))
        return [
            dict(row)
            for row in self.connection.execute(
                query, {str(index): value for index, value in enumerate(params, 1)}
            )
        ]


class Activations:
    def __init__(self):
        self.sessions = []
        self.error = None

    async def read(self, session_id):
        self.sessions.append(session_id)
        if self.error:
            raise self.error
        return (activation(),)


class Publisher:
    def __init__(self):
        self.requests = []
        self.error = None

    async def publish(self, request):
        if self.error:
            raise self.error
        self.requests.append(request)


@pytest.fixture
def source():
    database = Database()
    activations = Activations()
    yield database, activations, SessionCompactProjectionSource(database, activations)
    database.connection.close()


def applied(event_id):
    return ModelSessionContentProjectionAppliedEvent(
        event_id=event_id, rows_upserted=1, projected=True
    )


@pytest.mark.asyncio
async def test_resolves_event_and_reads_only_its_session_compact_rows(source):
    database, activations, reader = source
    own = chunks(package(), 3)
    for chunk in own:
        database.add(chunk)
    database.add(
        own[0].model_copy(update={"event_id": "foreign", "session_id": "other"})
    )
    database.add(own[0].model_copy(update={"event_id": "prompt"}), kind="prompt")
    resolved = await reader.read_applied(applied(own[-1].event_id))
    assert resolved.session_id == SESSION
    assert {chunk.event_id for chunk in resolved.snapshot.chunks} == {
        c.event_id for c in own
    }
    assert resolved.snapshot.activations == (activation(),)
    assert activations.sessions == [SESSION]
    assert len(database.reads) == 1
    assert database.reads[0][1] == (own[-1].event_id,)


@pytest.mark.asyncio
async def test_unknown_event_refuses_without_reading_activations(source):
    database, activations, reader = source
    with pytest.raises(ValueError, match=r"applied event.*not projected"):
        await reader.read_applied(applied("missing' OR 1=1 --"))
    assert activations.sessions == []
    assert database.reads[0][1] == ("missing' OR 1=1 --",)


@pytest.mark.asyncio
async def test_noncompact_trigger_carries_newest_capture_for_capture_gap(source):
    database, _, reader = source
    own = chunks(package())
    database.add(own[0])
    later = CAPTURED + timedelta(minutes=1)
    database.add(
        own[0].model_copy(update={"event_id": "turn", "emitted_at": later}),
        kind="prompt",
    )
    resolved = await reader.read_applied(applied("turn"))
    assert resolved.snapshot.newest_captured_at == later
    publisher = Publisher()
    out = await HandlerContextInstallConfirmTrigger().handle_projected(
        applied("turn"), reader, publisher
    )
    assert out.request is not None
    assert out.request.newest_captured_at == later
    assert publisher.requests == [out.request]


@pytest.mark.asyncio
async def test_projected_input_publishes_unwrapped_request_and_installs(source):
    database, _, reader = source
    own = chunks(package(), 3)
    for chunk in own:
        database.add(chunk)
    publisher = Publisher()
    result = await HandlerContextInstallConfirmTrigger().handle_projected(
        applied(own[-1].event_id), reader, publisher
    )
    assert result.outcome is EnumInstallConfirmTriggerOutcome.REQUEST_READY
    assert publisher.requests == [result.request]
    confirmed = HandlerContextInstallConfirm().handle(publisher.requests[0])
    assert confirmed.facts[0].fact is EnumInstallFact.INSTALLED
    assert confirmed.facts[0].boundary_uuid


@pytest.mark.asyncio
async def test_partial_package_publishes_nothing_then_final_chunk_retries(source):
    database, _, reader = source
    own = chunks(package(), 3)
    database.add(own[0])
    publisher = Publisher()
    handler = HandlerContextInstallConfirmTrigger()
    first = await handler.handle_projected(applied(own[0].event_id), reader, publisher)
    assert first.outcome is EnumInstallConfirmTriggerOutcome.ALL_PACKAGES_UNREADABLE
    assert publisher.requests == []
    for chunk in own[1:]:
        database.add(chunk)
    second = await handler.handle_projected(
        applied(own[-1].event_id), reader, publisher
    )
    assert second.outcome is EnumInstallConfirmTriggerOutcome.REQUEST_READY
    assert len(publisher.requests) == 1


@pytest.mark.asyncio
async def test_no_compact_rows_publish_nothing(source):
    database, _, reader = source
    database.add(chunks(package())[0], kind="prompt")
    publisher = Publisher()
    result = await HandlerContextInstallConfirmTrigger().handle_projected(
        applied(chunks(package())[0].event_id), reader, publisher
    )
    assert result.outcome is EnumInstallConfirmTriggerOutcome.NO_COMPACT_RECORDS
    assert publisher.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["activation", "publisher"])
async def test_dependency_failures_propagate_for_runtime_retry(source, failure):
    database, activations, reader = source
    own = chunks(package())[0]
    database.add(own)
    publisher = Publisher()
    target = activations if failure == "activation" else publisher
    target.error = RuntimeError("transport unavailable")
    with pytest.raises(RuntimeError, match="transport unavailable"):
        await HandlerContextInstallConfirmTrigger().handle_projected(
            applied(own.event_id), reader, publisher
        )
    assert publisher.requests == []


@pytest.mark.asyncio
async def test_replaying_applied_event_emits_identical_request(source):
    database, _, reader = source
    own = chunks(package())[0]
    database.add(own)
    publisher = Publisher()
    handler = HandlerContextInstallConfirmTrigger()
    for _ in range(2):
        await handler.handle_projected(applied(own.event_id), reader, publisher)
    assert (
        publisher.requests[0].model_dump_json()
        == publisher.requests[1].model_dump_json()
    )


@pytest.mark.parametrize("rows", [0, -1, True, "1"])
def test_zero_write_or_untyped_ack_is_refused(rows):
    with pytest.raises(ValidationError):
        ModelSessionContentProjectionAppliedEvent(event_id="event", rows_upserted=rows)
