# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-17426: the two savings aggregates the arrival page reads are bus-backed.

WHAT THIS CLOSES

``onex.snapshot.projection.delegation.savings.v1`` and
``onex.snapshot.projection.cost.savings-overview.v1`` are the two exposures the
customer arrival page reads, and both answered
``503 {"error":"not_yet_bus_backed","migration_ticket":"OMN-15800"}`` -- naming
a ticket that closed on 2026-08-24 having deferred these families to "a
follow-up ticket" that was never filed. This is that follow-up.

The projection API holds no DB handle by design (OMN-15800 seam B), so an
exposure is servable only once its writer republishes it on the topic the
SnapshotCache reads. Flipping ``bus_backed`` alone would turn an honest refusal
into a confident empty page -- the failure OMN-15864 exists to prevent -- so
the flag, the key, the serving-side tenant scope and the publish site are
asserted here together.

WHY THE PUBLISH SITE IS NOT GATED ON THIS RUNNER'S OWN WRITE

The delegation runner republishes only when the apply wrote the one table its
aggregates read. That gate would be WRONG here and the difference is the point
of ``test_a_delegation_terminal_that_banks_no_saving_still_republishes``: both
savings aggregates also read ``delegation_events``, which a different node
writes from the SAME terminal this runner consumes, and
``_project_canonical_delegation_savings`` returns truthfully-empty when no
counterfactual can be derived or the saving is <= 0. Gating on this runner's
own write would leave exactly those runs -- real delegations that banked no
saving -- permanently invisible on the page while onex-api returned them.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from omnimarket.nodes.node_projection_savings.handlers.handler_savings import (
    SNAPSHOT_GRAIN_COLUMN,
    SNAPSHOT_TENANT_COLUMN,
    SavingsProjectionRunner,
)
from omnimarket.projection.discovery import load_projection_exposures_from_contract
from omnimarket.projection.runner import MessageMeta
from omnimarket.projection.snapshot_cache import SnapshotCache
from omnimarket.projection.snapshot_publisher import encode_snapshot_delta

pytestmark = pytest.mark.unit

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "omnimarket"
    / "nodes"
    / "node_projection_savings"
    / "contract.yaml"
)

SAVINGS_TOPIC = "onex.snapshot.projection.delegation.savings.v1"
OVERVIEW_TOPIC = "onex.snapshot.projection.cost.savings-overview.v1"
SERIES_TOPIC = "onex.snapshot.projection.delegation.savings-series.v1"
ESTIMATES_TOPIC = "onex.snapshot.projection.savings.v1"
SERIES_TABLE = "projection_delegation_savings_series"

#: The two exposures this ticket converts, and the view each is served from.
CONVERTED = {
    SAVINGS_TOPIC: "projection_delegation_savings",
    OVERVIEW_TOPIC: "projection_cost_savings_overview",
}

TENANT = "52cd15be-b7c8-4beb-93f3-d267e5d2c875"
OTHER_TENANT = "11111111-1111-4111-8111-111111111111"
EMPTY_TENANT = "33333333-3333-4333-8333-333333333333"


def _exposures() -> dict[str, Any]:
    contract = yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8"))
    return {
        exposure.topic: exposure
        for exposure in load_projection_exposures_from_contract(
            contract, "projection_savings", _CONTRACT_PATH
        )
    }


def _meta() -> MessageMeta:
    return MessageMeta(
        fallback_id="evt-omn17426",
        topic="onex.evt.omnibase-infra.delegation-completed.v1",
        partition=3,
        offset=41,
    )


# ---------------------------------------------------------------------------
# 1. The contract declaration.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topic", sorted(CONVERTED))
def test_the_arrival_page_exposures_are_bus_backed_and_tenant_scoped(
    topic: str,
) -> None:
    exposure = _exposures()[topic]
    assert exposure.table == CONVERTED[topic]
    assert exposure.bus_backed is True
    # Grain AND tenant. Grain alone would compact every tenant's aggregate onto
    # one record and serve whichever tenant wrote last to all of them; a key
    # taken from the view's own changing columns would mint a record per apply.
    assert exposure.key_columns == (SNAPSHOT_GRAIN_COLUMN, SNAPSHOT_TENANT_COLUMN)
    # The serving half. Without it SnapshotCache.get_rows applies no filter and
    # hands a caller every tenant's record under one topic.
    assert exposure.tenant_column == SNAPSHOT_TENANT_COLUMN
    assert exposure.tenant_scoped is True
    assert exposure.limit == 1
    # tenant_column must be servable off the published row, which means the
    # view must actually emit it (migration 089).
    assert SNAPSHOT_TENANT_COLUMN in exposure.columns


def test_the_unconverted_exposures_stay_refused() -> None:
    """The positive control: conversion is per-exposure, not a blanket flip."""
    exposures = _exposures()
    # OMN-17882 TI-AC1 converts the tenant/day series with its publish site;
    # the unrelated per-row exposure remains refused without its own writer.
    assert exposures[ESTIMATES_TOPIC].bus_backed is False


def test_the_daily_series_is_backed_by_tenant_and_bucket() -> None:
    """OMN-17882: flip the series only with its row-level bus key and reader scope."""
    exposure = _exposures()[SERIES_TOPIC]
    assert exposure.bus_backed is True
    assert exposure.key_columns == (SNAPSHOT_TENANT_COLUMN, "bucket")
    assert exposure.tenant_column == SNAPSHOT_TENANT_COLUMN
    assert exposure.tenant_scoped is True
    assert exposure.limit == 365
    assert exposure.columns[-1] == SNAPSHOT_TENANT_COLUMN


def test_every_converted_snapshot_topic_is_declared_as_published() -> None:
    """A bus_backed exposure whose topic no node claims to publish is a page
    that refuses forever while reporting itself as backed."""
    contract = yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8"))
    published = set(contract["event_bus"]["publish_topics"])
    assert (CONVERTED.keys() | {SERIES_TOPIC}) <= published


# ---------------------------------------------------------------------------
# 2. The construction guard -- the flag cannot outrun the publish site.
# ---------------------------------------------------------------------------


def test_the_runner_binds_both_aggregates_and_not_the_per_row_exposure() -> None:
    runner = SavingsProjectionRunner()
    assert {e.topic for e in runner._aggregate_exposures} == set(CONVERTED)
    assert runner._series_exposure.topic == SERIES_TOPIC
    assert runner._series_exposure.key_columns == (SNAPSHOT_TENANT_COLUMN, "bucket")
    # savings.v1 is bus_backed: false today, so there is no per-row binding.
    # The binding is by TABLE, not by "the first bus_backed exposure": that
    # earlier single-binding would now resolve to an aggregate and publish a
    # whole view through the per-row upsert site.
    assert runner._snapshot_exposure is None


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"key_columns": ["tenant_id"]}, "no publish site"),
        ({"limit": 12}, "collapse onto a single cache entry"),
        ({"tenant_column": None}, "answered unscoped"),
    ],
)
def test_an_aggregate_without_a_valid_publish_site_refuses_to_construct(
    tmp_path: Path, mutation: dict[str, Any], expected: str
) -> None:
    """Converting an exposure is adding a publish call, never a one-line edit.

    Each mutation is individually survivable-looking and individually produces
    a writer that serves a confident wrong page: a key without the grain
    compacts every apply onto its own record, a limit above one collapses a
    multi-row exposure onto a single cache entry, and a missing tenant_column
    publishes per tenant while serving unscoped.
    """
    contract = yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8"))
    for exposure in contract["projection_api"]["exposures"]:
        if exposure["topic"] == OVERVIEW_TOPIC:
            exposure.update(mutation)
            if mutation.get("tenant_column", "") is None:
                del exposure["tenant_column"]
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")

    with pytest.raises(ValueError, match=expected):
        SavingsProjectionRunner(contract_path=path)


# ---------------------------------------------------------------------------
# 3. The publish site.
# ---------------------------------------------------------------------------


class _Publishes:
    """Captures publish_snapshot_delta calls instead of reaching a broker."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, exposure: Any, **kwargs: Any) -> bool:
        self.calls.append({"exposure": exposure, **kwargs})
        return True


def _runner_with(rows: list[dict[str, Any]]) -> tuple[Any, AsyncMock, _Publishes]:
    runner = SavingsProjectionRunner()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=rows)
    runner._db = db
    published = _Publishes()
    runner.publish_snapshot_delta = published  # type: ignore[method-assign]
    return runner, db, published


@pytest.mark.asyncio
async def test_the_republish_reads_each_view_under_the_write_tenant() -> None:
    row = {SNAPSHOT_GRAIN_COLUMN: OVERVIEW_TOPIC, SNAPSHOT_TENANT_COLUMN: TENANT}
    runner, db, published = _runner_with([row])

    await runner._publish_aggregate_snapshots(_meta(), tenant=TENANT)

    statements = [str(call.args[0]) for call in db.execute.await_args_list]
    assert len(statements) == 2, statements
    for statement, exposure in zip(
        statements, runner._aggregate_exposures, strict=True
    ):
        assert f"FROM {exposure.table} agg" in statement
        # The explicit predicate, not just the session scope: a superuser or
        # BYPASSRLS reader is not narrowed by the GUC, and a bare LIMIT 1 would
        # hand it whichever row sorted first while the header claimed `tenant`.
        assert f"WHERE agg.{SNAPSHOT_TENANT_COLUMN} = $2" in statement
        # tenant_id is read off the view, never selected a second time beside
        # agg.* -- two columns of one name in one record and the survivor is a
        # property of the driver.
        assert statement.count(SNAPSHOT_TENANT_COLUMN) == 1
    for call in db.execute.await_args_list:
        assert call.args[2] == TENANT
        assert call.kwargs["tenant"] == TENANT

    assert [call["tenant_id"] for call in published.calls] == [TENANT, TENANT]
    assert [call["op"] for call in published.calls] == ["upsert", "upsert"]
    # The ordering authority is the SOURCE message's own coordinates.
    assert published.calls[0]["source_partition"] == 3
    assert published.calls[0]["source_offset"] == 41


@pytest.mark.asyncio
async def test_a_view_with_no_row_for_this_tenant_publishes_nothing() -> None:
    """An aggregate that cannot be measured stays absent from the page rather
    than being rendered as a zero, which reads as a quiet period."""
    runner, db, published = _runner_with([])

    await runner._publish_aggregate_snapshots(_meta(), tenant=OTHER_TENANT)

    assert db.execute.await_count == 2, "positive control: both views were read"
    assert published.calls == []


@pytest.mark.asyncio
async def test_a_delegation_terminal_that_banks_no_saving_still_republishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the delegation runner's write-gated trigger would have lost.

    A FAILED terminal derives no counterfactual, so ``_apply_event`` returns
    True having written no ``savings_estimates`` row. ``delegation_events``
    still gained that run, from the same event, written by another node -- and
    both aggregates read it. A republish gated on this runner's own write would
    leave that run invisible on the arrival page forever.
    """
    runner, _db, published = _runner_with(
        [{SNAPSHOT_GRAIN_COLUMN: OVERVIEW_TOPIC, SNAPSHOT_TENANT_COLUMN: TENANT}]
    )
    wrote: list[str] = []

    async def _record_write(**_kwargs: Any) -> None:
        wrote.append("row")

    monkeypatch.setattr(runner, "_resolve_row_tenant", AsyncMock(return_value=TENANT))
    monkeypatch.setattr(runner, "_upsert_savings_estimate", _record_write)

    ok = await runner.project_event(
        "onex.evt.omnibase-infra.delegation-failed.v1",
        {
            "correlation_id": "d418ec56-d595-466a-a83b-ec8ed33fa19f",
            "task_type": "code_review",
            "model_used": "qwen2.5-coder",
            "success": False,
            # No served tokens, so no counterfactual can be derived and the
            # apply banks nothing. delegation_events still gained the run.
            "cumulative_input_tokens": 0,
            "cumulative_output_tokens": 0,
        },
        _meta(),
    )

    assert ok is True
    assert wrote == [], "positive control: the apply banked no savings row"
    assert {call["exposure"].topic for call in published.calls} == (
        set(CONVERTED) | {SERIES_TOPIC}
    )


@pytest.mark.asyncio
async def test_a_quarantined_event_republishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_route_malformed_to_dlq`` returns True so the offset commits. Without
    the DLQ flag that is indistinguishable from an apply, and a malformed-event
    storm would mint one record per message on a compacted topic."""
    runner, _db, published = _runner_with(
        [{SNAPSHOT_GRAIN_COLUMN: OVERVIEW_TOPIC, SNAPSHOT_TENANT_COLUMN: TENANT}]
    )
    monkeypatch.setattr(runner, "_resolve_row_tenant", AsyncMock(return_value=TENANT))
    monkeypatch.setattr(runner, "get_publish_fn", AsyncMock(return_value=None))

    ok = await runner.project_event(
        "onex.evt.omnibase-infra.savings-estimated.v1",
        {"model_local": "qwen"},  # no session_id -> quarantined
        _meta(),
    )

    assert ok is True
    assert runner._dlq_routed is True, "positive control: the event was quarantined"
    assert published.calls == []


# ---------------------------------------------------------------------------
# 4. The wire shape -- what actually lands on the compacted topic.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("topic", sorted(CONVERTED))
def test_the_compacted_key_is_the_grain_and_the_tenant(topic: str) -> None:
    """One live record per tenant per aggregate, forever -- not one per apply,
    and not one shared by every tenant."""
    exposure = _exposures()[topic]
    row = {
        SNAPSHOT_GRAIN_COLUMN: topic,
        SNAPSHOT_TENANT_COLUMN: TENANT,
        "latest_projection_updated_at": "2026-09-12T12:00:00+00:00",
    }
    message = encode_snapshot_delta(
        exposure,
        op="upsert",
        row=row,
        key=None,
        source_event_id="evt-omn17426",
        source_topic="onex.evt.omnibase-infra.delegation-completed.v1",
        source_partition=0,
        source_offset=1,
        observed_at="2026-09-12T12:00:00+00:00",
        tenant_id=TENANT,
    )
    assert message is not None
    assert message.topic == topic
    key = message.key.decode("utf-8")
    assert topic in key
    assert TENANT in key
    # The control: another tenant's aggregate is a DIFFERENT compaction key, so
    # the two coexist instead of overwriting each other.
    other = encode_snapshot_delta(
        exposure,
        op="upsert",
        row={**row, SNAPSHOT_TENANT_COLUMN: OTHER_TENANT},
        key=None,
        source_event_id="evt-omn17426",
        source_topic="onex.evt.omnibase-infra.delegation-completed.v1",
        source_partition=0,
        source_offset=2,
        observed_at="2026-09-12T12:00:00+00:00",
        tenant_id=OTHER_TENANT,
    )
    assert other is not None
    assert other.key != message.key


@pytest.mark.asyncio
async def test_terminal_apply_publishes_and_serves_only_its_tenant_series_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Golden chain for the series publish site and tenant-scoped cache read.

    Failure modes: the bus flag gets ahead of a publish site; a view read omits
    its explicit tenant predicate and publishes another tenant's rows; the
    snapshot key omits tenant or bucket so rows collide; the cached rows cannot
    be read back by their tenant; or the cache answers an unseeded tenant.
    The two distinct source rows are the positive control for the unscoped-query
    mutation: removing the SQL predicate makes each apply attempt to publish
    both tenants and breaks the exact per-apply assertion below.
    """
    first_day = datetime(2026, 1, 1, tzinfo=UTC)
    source_rows = [
        {
            "bucket": (first_day + timedelta(days=day)).isoformat(),
            "actual_cost_usd": 0.01,
            "baseline_cost_usd": 0.78,
            "savings_usd": 0.77,
            "task_count": 1,
            SNAPSHOT_TENANT_COLUMN: TENANT,
        }
        for day in range(365)
    ] + [
        {
            "bucket": first_day.isoformat(),
            "actual_cost_usd": 0.02,
            "baseline_cost_usd": 0.35,
            "savings_usd": 0.33,
            "task_count": 1,
            SNAPSHOT_TENANT_COLUMN: OTHER_TENANT,
        }
    ]
    assert len(source_rows) == 366
    assert source_rows[0]["savings_usd"] != source_rows[-1]["savings_usd"]

    messages: list[dict[str, Any]] = []
    series_queries: list[str] = []

    async def _apply_for_tenant(tenant: str, offset: int) -> None:
        runner = SavingsProjectionRunner()

        async def _execute(
            query: str, *params: Any, **kwargs: Any
        ) -> list[dict[str, Any]]:
            if SERIES_TABLE not in query:
                return []
            series_queries.append(query)
            match = re.search(
                r"\bWHERE\b.*?\btenant_id\s*=\s*\$(\d+)",
                query,
                re.IGNORECASE | re.DOTALL,
            )
            if match is None:
                # Deliberately model the unsafe query: without the explicit
                # predicate a bypass-RLS reader sees both tenant rows.
                return list(source_rows)
            requested = kwargs.get("tenant")
            if requested is None:
                parameter_index = int(match.group(1)) - 1
                requested = params[parameter_index]
            return [
                row
                for row in source_rows
                if row[SNAPSHOT_TENANT_COLUMN] == str(requested)
            ]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute)
        runner._db = db
        monkeypatch.setattr(
            runner, "_resolve_row_tenant", AsyncMock(return_value=tenant)
        )
        # The terminal projection still runs through project_event and its
        # canonical event path; this write seam is held inert because the test
        # is about the independently read series view.
        monkeypatch.setattr(
            runner, "_upsert_savings_estimate", AsyncMock(return_value=None)
        )

        producer = MagicMock()

        async def _send_and_wait(
            topic: str,
            value: bytes | None = None,
            key: bytes | None = None,
            headers: list[tuple[str, bytes]] | None = None,
        ) -> None:
            messages.append(
                {"topic": topic, "value": value, "key": key, "headers": headers}
            )

        producer.send_and_wait = AsyncMock(side_effect=_send_and_wait)
        runner._producer = producer
        topic = runner._topic_delegation_completed
        assert topic, "positive control: the contract declares the terminal topic"
        payload = {
            "correlation_id": f"d418ec56-d595-466a-a83b-{offset:012d}",
            "tenant_id": tenant,
            "task_type": "code_review",
            "model_used": "qwen2.5-coder",
            "success": True,
            "cumulative_input_tokens": 100,
            "cumulative_output_tokens": 100,
            "cumulative_attempt_cost": 0.01,
        }
        meta = MessageMeta(
            fallback_id=f"evt-series-{offset}",
            topic=topic,
            partition=0,
            offset=offset,
        )
        assert await runner.project_event(topic, payload, meta) is True

    started = time.perf_counter()
    await _apply_for_tenant(TENANT, 41)
    tenant_a_elapsed = time.perf_counter() - started
    first_series_messages = [m for m in messages if m["topic"] == SERIES_TOPIC]
    assert len(first_series_messages) == 365, (
        "one apply must publish every one of this tenant's 365 day rows; an absent "
        "publish site or an unscoped query is a failure"
    )
    assert {
        json.loads(message["value"])["row"][SNAPSHOT_TENANT_COLUMN]
        for message in first_series_messages
    } == {TENANT}
    print(
        "OMN-17882 series publish measurement: "
        f"tenant={TENANT} publish_count={len(first_series_messages)} "
        f"elapsed_seconds={tenant_a_elapsed:.6f}"
    )

    await _apply_for_tenant(OTHER_TENANT, 42)
    series_messages = [m for m in messages if m["topic"] == SERIES_TOPIC]
    assert len(series_messages) == 366, "365 rows for A and one row for B"
    rows_by_message_tenant = [
        json.loads(message["value"])["row"][SNAPSHOT_TENANT_COLUMN]
        for message in series_messages
    ]
    assert rows_by_message_tenant.count(TENANT) == 365
    assert rows_by_message_tenant.count(OTHER_TENANT) == 1
    assert all(
        re.search(
            r"\bWHERE\b.*?\btenant_id\s*=\s*\$\d+",
            query,
            re.IGNORECASE | re.DOTALL,
        )
        for query in series_queries
    ), "each series read must carry the explicit tenant predicate"

    await _apply_for_tenant(EMPTY_TENANT, 43)
    series_messages = [m for m in messages if m["topic"] == SERIES_TOPIC]
    assert len(series_messages) == 366, "an empty tenant produces no snapshot delta"

    exposure = _exposures()[SERIES_TOPIC]
    assert exposure.key_columns == (SNAPSHOT_TENANT_COLUMN, "bucket")
    cache = SnapshotCache(
        {SERIES_TOPIC: exposure},
        bootstrap_servers="unused:9092",
        group_id="test-omn17882-series",
    )
    for message in series_messages:
        assert message["key"] is not None
        assert message["headers"] is not None
        cache.apply_message(
            message["topic"],
            message["key"],
            message["value"],
            message["headers"],
        )
    cache._state[SERIES_TOPIC].bootstrap_complete = True

    tenant_a_rows = cache.get_rows(
        SERIES_TOPIC,
        tenant_column=SNAPSHOT_TENANT_COLUMN,
        tenant_id=TENANT,
        unbounded=True,
    )
    tenant_b_rows = cache.get_rows(
        SERIES_TOPIC,
        tenant_column=SNAPSHOT_TENANT_COLUMN,
        tenant_id=OTHER_TENANT,
        unbounded=True,
    )
    assert len(tenant_a_rows) == 365
    assert {row["savings_usd"] for row in tenant_a_rows} == {0.77}
    assert [row["savings_usd"] for row in tenant_b_rows] == [0.33]
    assert (
        cache.get_rows(
            SERIES_TOPIC,
            tenant_column=SNAPSHOT_TENANT_COLUMN,
            tenant_id=EMPTY_TENANT,
            unbounded=True,
        )
        == []
    )
