# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Short-lived ledger receipt listeners without broker consumer groups.

Every caller must see every reply and match its own envelope ID. These
listeners need neither shared assignment nor durable offsets. Joining fresh
Kafka groups for them retains two groups per invocation (OMN-20275).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from aiokafka import AIOKafkaConsumer
from omnibase_infra.event_bus.kafka_auth import build_aiokafka_auth_kwargs
from omnibase_infra.event_bus.models.config.model_kafka_event_bus_config import (
    ModelKafkaEventBusConfig,
)

from omnimarket.delegated_test_loop.lab_run_bus import ProtocolBusMessage


async def subscribe_reply(
    config: ModelKafkaEventBusConfig,
    physical_topic: str,
    on_message: Callable[[ProtocolBusMessage], Awaitable[None]],
) -> Callable[[], Awaitable[None]]:
    """Position at the current end before permitting the command publish.

    aiokafka's NoGroupCoordinator assigns all partitions locally when
    group_id is None. Concurrent callers therefore each receive their own
    copy, including when they run on the same host.
    """
    consumer = AIOKafkaConsumer(
        physical_topic,
        bootstrap_servers=config.bootstrap_servers,
        group_id=None,
        enable_auto_commit=False,
        auto_offset_reset="latest",
        max_partition_fetch_bytes=config.max_partition_fetch_bytes,
        **({"api_version": config.api_version} if config.api_version else {}),
        **build_aiokafka_auth_kwargs(config),
    )
    try:
        async with asyncio.timeout(config.timeout_seconds):
            await consumer.start()
            # Resolve the end now: lazy 'latest' resolution after publishing
            # could skip a receipt that arrived before the first poll.
            await consumer.seek_to_end()
    except BaseException:
        await consumer.stop()
        raise

    async def consume() -> None:
        async for message in consumer:
            await on_message(message)

    task = asyncio.create_task(consume(), name="work-ledger-replies")
    stopped = False

    async def unsubscribe() -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        task.cancel()
        try:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            await consumer.stop()

    return unsubscribe
