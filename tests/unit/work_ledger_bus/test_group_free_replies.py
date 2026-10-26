# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Receipt listeners never join or retain Kafka groups (OMN-20275)."""

import asyncio
from types import SimpleNamespace

import pytest
from omnibase_infra.event_bus.models.config.model_kafka_event_bus_config import (
    ModelKafkaEventBusConfig,
)

from omnimarket.nodes.node_work_ledger_append_effect.handlers import reply_consumer

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("authenticated", [False, True])
def test_reply_listener_positions_before_return_and_closes(monkeypatch, authenticated):
    async def scenario():
        events = []
        records = asyncio.Queue()

        class Consumer:
            def __init__(self, topic, **kwargs):
                events.append(("config", topic, kwargs))

            async def start(self):
                events.append("started")

            async def seek_to_end(self):
                events.append("positioned")

            def __aiter__(self):
                return self

            async def __anext__(self):
                return await records.get()

            async def stop(self):
                events.append("stopped")

        monkeypatch.setattr(reply_consumer, "AIOKafkaConsumer", Consumer)
        received = asyncio.Event()

        async def on_message(message):
            assert message.value == b"receipt"
            received.set()

        config = ModelKafkaEventBusConfig(
            bootstrap_servers="localhost:9092",
            **(
                {
                    "security_protocol": "SASL_PLAINTEXT",
                    "sasl_mechanism": "SCRAM-SHA-256",
                    "sasl_plain_username": "unit-user",
                    "sasl_plain_password": "unit-password",
                }
                if authenticated
                else {}
            ),
        )
        unsubscribe = await reply_consumer.subscribe_reply(
            config, "receipts", on_message
        )
        assert events[1:] == ["started", "positioned"]
        assert events[0][2]["group_id"] is None
        assert events[0][2]["enable_auto_commit"] is False
        if authenticated:
            assert events[0][2]["security_protocol"] == "SASL_PLAINTEXT"
            assert events[0][2]["sasl_mechanism"] == "SCRAM-SHA-256"
            assert events[0][2]["sasl_plain_username"] == "unit-user"
            assert events[0][2]["sasl_plain_password"] == "unit-password"
        await records.put(SimpleNamespace(value=b"receipt"))
        await asyncio.wait_for(received.wait(), 1)
        await unsubscribe()
        assert events[-1] == "stopped"
        await unsubscribe()
        assert events.count("stopped") == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_at", ["start", "position"])
def test_failed_subscription_closes_consumer(monkeypatch, fail_at):
    async def scenario():
        stopped = []

        class Consumer:
            def __init__(self, *args, **kwargs):
                pass

            async def start(self):
                if fail_at == "start":
                    raise TimeoutError("metadata unavailable")

            async def seek_to_end(self):
                raise TimeoutError("metadata unavailable")

            async def stop(self):
                stopped.append(True)

        monkeypatch.setattr(reply_consumer, "AIOKafkaConsumer", Consumer)

        async def on_message(message):
            raise AssertionError("no record expected")

        with pytest.raises(TimeoutError, match="metadata unavailable"):
            await reply_consumer.subscribe_reply(
                ModelKafkaEventBusConfig(bootstrap_servers="localhost:9092"),
                "receipts",
                on_message,
            )
        assert stopped == [True]

    asyncio.run(scenario())


def test_kafka_caller_uses_independent_group_free_listeners(monkeypatch):
    from omnibase_infra.event_bus.event_bus_kafka import EventBusKafka

    from omnimarket.work_ledger_bus.bus import WorkLedgerAppendCaller

    async def scenario():
        subscriptions = []
        stopped = []

        async def subscribe(config, topic, on_message):
            subscriptions.append((config, topic, on_message))

            async def unsubscribe():
                stopped.append(topic)

            return unsubscribe

        monkeypatch.setattr(reply_consumer, "subscribe_reply", subscribe)
        bus = EventBusKafka(
            config=ModelKafkaEventBusConfig(bootstrap_servers="localhost:9092")
        )
        first = WorkLedgerAppendCaller(bus, principal="developer")
        second = WorkLedgerAppendCaller(bus, principal="developer")
        await first.start()
        await second.start()
        assert len(subscriptions) == 4
        assert subscriptions[0][1] == subscriptions[2][1]
        assert subscriptions[1][1] == subscriptions[3][1]
        assert subscriptions[0][2] is not subscriptions[2][2]
        assert all(config is bus.config for config, _, _ in subscriptions)
        await first.stop()
        await second.stop()
        assert len(stopped) == 4

    asyncio.run(scenario())


def test_partial_caller_start_closes_first_listener(monkeypatch):
    from omnibase_infra.event_bus.event_bus_kafka import EventBusKafka

    from omnimarket.work_ledger_bus.bus import WorkLedgerAppendCaller

    async def scenario():
        starts = []
        stopped = []

        async def subscribe(config, topic, on_message):
            starts.append(topic)
            if len(starts) == 2:
                raise TimeoutError("second listener failed")

            async def unsubscribe():
                stopped.append(topic)

            return unsubscribe

        monkeypatch.setattr(reply_consumer, "subscribe_reply", subscribe)
        caller = WorkLedgerAppendCaller(
            EventBusKafka(
                config=ModelKafkaEventBusConfig(bootstrap_servers="localhost:9092")
            ),
            principal="developer",
        )
        with pytest.raises(TimeoutError, match="second listener failed"):
            await caller.start()
        assert stopped == starts[:1]
        await caller.stop()
        assert len(stopped) == 1

    asyncio.run(scenario())


def test_cancelled_positioning_closes_consumer(monkeypatch):
    async def scenario():
        positioning = asyncio.Event()
        stopped = []

        class Consumer:
            def __init__(self, *args, **kwargs):
                pass

            async def start(self):
                pass

            async def seek_to_end(self):
                positioning.set()
                await asyncio.Event().wait()

            async def stop(self):
                stopped.append(True)

        monkeypatch.setattr(reply_consumer, "AIOKafkaConsumer", Consumer)

        async def on_message(message):
            raise AssertionError("no record expected")

        task = asyncio.create_task(
            reply_consumer.subscribe_reply(
                ModelKafkaEventBusConfig(bootstrap_servers="localhost:9092"),
                "receipts",
                on_message,
            )
        )
        await asyncio.wait_for(positioning.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped == [True]

    asyncio.run(scenario())
