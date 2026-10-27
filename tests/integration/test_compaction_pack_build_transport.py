# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Run the compaction transport scenarios unchanged on an isolated Kafka binding.

Set ONEX_TEST_COMPACTION_KAFKA_BOOTSTRAP to a disposable loopback broker. Each
scenario gets its own physical topic namespace; no deployed runtime consumes it.
The projection and factor sources remain labelled fixtures, so these tests do
not assert that a recorded session builds on the deployed dev runtime.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from omnibase_infra.event_bus.event_bus_kafka import EventBusKafka
from omnibase_infra.event_bus.models.config.model_kafka_event_bus_config import (
    ModelKafkaEventBusConfig,
)
from omnibase_infra.topics.topic_namespace import apply_topic_namespace

from omnimarket.nodes.node_context_compaction_orchestrator.handlers.compaction_runtime_binding import (
    load_compaction_pack_topics,
)
from tests.nodes.node_context_compaction_orchestrator.bus_fixture import BusHarness
from tests.nodes.node_context_compaction_orchestrator.test_compaction_pack_build_bus import (
    test_compaction_pack_build_bus_failed_reply_is_native,
    test_compaction_pack_build_bus_one_command_then_built,
    test_compaction_pack_build_bus_revision_probe_matches_request,
    test_compaction_pack_build_bus_transport_shares_the_deadline,
)

__all__ = [
    "test_compaction_pack_build_bus_failed_reply_is_native",
    "test_compaction_pack_build_bus_one_command_then_built",
    "test_compaction_pack_build_bus_revision_probe_matches_request",
    "test_compaction_pack_build_bus_transport_shares_the_deadline",
]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.kafka,
    pytest.mark.usefixtures("promoted_context_profile_routing"),
]


@pytest.fixture
def compaction_bus(monkeypatch: pytest.MonkeyPatch):
    bootstrap = os.environ.get("ONEX_TEST_COMPACTION_KAFKA_BOOTSTRAP")
    if not bootstrap:
        pytest.skip("set ONEX_TEST_COMPACTION_KAFKA_BOOTSTRAP to a disposable broker")
    host, separator, port = bootstrap.rpartition(":")
    if host != "127.0.0.1" or separator != ":" or not port.isdecimal():
        pytest.fail("compaction transport proof requires an explicit loopback broker")
    monkeypatch.setenv("KAFKA_TOPIC_NAMESPACE", "compact-" + uuid4().hex[:16])
    harnesses = []

    class PreparedKafkaBus(EventBusKafka):
        async def start(self):
            # Provision before subscription, as a deployed binding does. Topic
            # auto-creation can expose partitions before their metadata is ready.
            topics = load_compaction_pack_topics()
            admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
            await admin.start()
            try:
                await admin.create_topics(
                    [
                        NewTopic(apply_topic_namespace(topic), 1, 1)
                        for topic in (
                            topics.request_topic,
                            topics.completed_topic,
                            topics.failed_topic,
                        )
                    ]
                )
            finally:
                await admin.close()
            await super().start()

    def bus_factory():
        return PreparedKafkaBus(
            config=ModelKafkaEventBusConfig(
                bootstrap_servers=bootstrap,
                environment="integration-test",
                security_protocol="PLAINTEXT",
                auto_offset_reset="earliest",
                enable_idempotence=False,
            )
        )

    def factory(pack=None, **kwargs):
        harness = BusHarness(pack, bus_factory=bus_factory, **kwargs)
        harnesses.append(harness)
        return harness

    yield factory
    for harness in reversed(harnesses):
        harness.close()
