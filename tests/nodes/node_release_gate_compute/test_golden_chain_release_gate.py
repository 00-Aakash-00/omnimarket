# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Contract-resolved release gate round trip over the runtime's in-memory bus."""

from __future__ import annotations

import asyncio
import importlib
from importlib.resources import files
from typing import Any

import pytest
import yaml
from omnibase_core.event_bus.event_bus_inmemory import EventBusInmemory
from release_gate_builders import (
    TRAIN,
    agent_release_pr,
    load_policy,
    pr,
    request,
)

from omnimarket.models.model_release_gate import (
    ModelReleaseGateResult,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("facts", "actor", "verdict"),
    [
        (pr(), TRAIN, "TRAIN"),
        (agent_release_pr(), "lab-bot", "REFUSED"),
    ],
)
async def test_golden_and_refusal_chains(facts: Any, actor: str, verdict: str) -> None:
    contract = yaml.safe_load(
        files("omnimarket.nodes.node_release_gate_compute")
        .joinpath("contract.yaml")
        .read_text()
    )
    spec = contract["handler"]
    handler = getattr(importlib.import_module(spec["module"]), spec["class"])()
    request_topic = contract["runtime_dispatch"]["command_topic"]
    terminal_topic = contract["terminal_event"]
    assert request_topic == "onex.cmd.omnimarket.release-gate-check-requested.v1"
    assert contract["event_bus"]["subscribe_topics"] == [request_topic]
    assert terminal_topic == "onex.evt.omnimarket.release-gate-checked.v1"
    assert contract["event_bus"]["publish_topics"] == [terminal_topic]
    bus = EventBusInmemory()
    received: asyncio.Future[tuple[str, ModelReleaseGateResult]] = (
        asyncio.get_running_loop().create_future()
    )
    request_model = type(request(load_policy(), pull_request=facts))

    async def handle(message: Any) -> None:
        assert message.topic == request_topic
        result = handler.handle(request_model.model_validate_json(message.value))
        await bus.publish(
            terminal_topic, key=None, value=result.model_dump_json().encode()
        )

    async def collect(message: Any) -> None:
        received.set_result(
            (message.topic, ModelReleaseGateResult.model_validate_json(message.value))
        )

    await bus.start()
    try:
        unsubscribe_request = await bus.subscribe(
            request_topic, on_message=handle, group_id="gate-request"
        )
        unsubscribe_result = await bus.subscribe(
            terminal_topic, on_message=collect, group_id="gate-result"
        )
        try:
            await bus.publish(
                request_topic,
                key=None,
                value=request(
                    load_policy(),
                    pull_request=facts,
                    actor=actor,
                    actor_type="Bot" if actor == TRAIN else "User",
                )
                .model_dump_json()
                .encode(),
            )
            topic, result = await asyncio.wait_for(received, timeout=5)
            assert topic == terminal_topic
            assert result.verdict == verdict
            assert result.exit_code == (0 if result.allowed else 1)
        finally:
            await unsubscribe_result()
            await unsubscribe_request()
    finally:
        await bus.close()
