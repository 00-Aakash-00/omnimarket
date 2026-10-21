# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""A deployment can serve its morning phases on the run's own host.

``phase_host.placement: run_host`` wires one lab work host on the run's own bus
and addresses every phase unit to it: no capacity window, no placement, and the
phase agents start on the host the run runs on, with the agent transport's own
permission checks. The orchestrator still sends units only over the bus.
"""

from __future__ import annotations

import json

import pytest
from omnibase_core.event_bus.event_bus_inmemory import EventBusInmemory
from pydantic import JsonValue

from omnimarket.nodes.node_lab_work_unit_effect import protocols as lab_protocols
from omnimarket.nodes.node_lab_work_unit_effect.models import (
    EnumLabWorkUnitStatus,
    ModelLabWorkUnitReceipt,
    ModelLabWorkUnitRequest,
)
from omnimarket.nodes.node_morning_ground_state_orchestrator.handlers.handler_morning_phase_lab_work import (
    AGENT_LAUNCHER,
    RUN_HOST_PREFIX,
    HandlerMorningPhaseLabWork,
    MorningPhaseLabWorkError,
)
from omnimarket.nodes.node_morning_ground_state_orchestrator.models import (
    ModelMorningOverlay,
    ModelMorningPhaseHost,
    ModelMorningPhaseRequest,
)

from .helpers import public_overlay, request

SHA = "28219cf929b32bf51e0a83ac21d89caeed1a27d5"
TARGET: dict[str, JsonValue] = {
    "labWork": {
        "repo": "OmniNode-ai/omnimarket",
        "commit_sha": SHA,
        "lane": "morning-ground-state",
    }
}
RESULT: dict[str, JsonValue] = {
    "state": "stub",
    "detail": "fixture",
    "prs": [],
    "residuals": [],
    "delegation": {"delegated": 1, "runs": ["fixture"], "reason": ""},
}


def run_host_overlay(**phase_host: object) -> ModelMorningOverlay:
    base = public_overlay()
    return base.model_copy(
        update={
            "phase_host": ModelMorningPhaseHost.model_validate(
                {"placement": "run_host", **phase_host}
            )
        }
    )


class RecordingRunner:
    """Stands in for the host's shell runner; answers each unit with a fixed tail."""

    units: list[ModelLabWorkUnitRequest] = []
    hosts: list[str] = []
    tail = json.dumps(RESULT)
    exit_code = 0

    def __init__(self, work_root: object = None) -> None:
        del work_root

    def run(
        self, unit: ModelLabWorkUnitRequest, host_name: str
    ) -> ModelLabWorkUnitReceipt:
        RecordingRunner.units.append(unit)
        RecordingRunner.hosts.append(host_name)
        return ModelLabWorkUnitReceipt(
            work_unit_id=unit.work_unit_id,
            target_host=unit.target_host,
            host=host_name,
            repo=unit.repo,
            commit_sha=unit.commit_sha,
            lane=unit.lane,
            kind=unit.kind,
            status=EnumLabWorkUnitStatus.COMPLETED,
            exit_code=RecordingRunner.exit_code,
            log_path="/run/log",
            output_tail=RecordingRunner.tail,
        )


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> type[RecordingRunner]:
    RecordingRunner.units = []
    RecordingRunner.hosts = []
    RecordingRunner.tail = json.dumps(RESULT)
    RecordingRunner.exit_code = 0
    monkeypatch.setattr(lab_protocols, "LocalShellLabWorkExecutor", RecordingRunner)
    return RecordingRunner


def phase() -> ModelMorningPhaseRequest:
    return ModelMorningPhaseRequest(
        label="ground-state",
        phase="GroundState",
        model="opus",
        effort="high",
        prompt="derive the ground state",
        schema_definition={"type": "object"},
    )


@pytest.mark.unit
def test_overlay_phase_host_defaults_to_pool_and_validates() -> None:
    assert public_overlay().phase_host == ModelMorningPhaseHost()
    assert ModelMorningPhaseHost().placement == "pool"
    with pytest.raises(ValueError, match="placement"):
        ModelMorningPhaseHost.model_validate({"placement": "anywhere"})
    with pytest.raises(ValueError, match="permission_mode"):
        ModelMorningPhaseHost.model_validate({"permission_mode": "bypassPermissions"})
    with pytest.raises(ValueError, match="host"):
        ModelMorningPhaseHost.model_validate({"placement": "run_host", "host": "x"})


@pytest.mark.unit
def test_overlay_document_carries_phase_host() -> None:
    base = public_overlay()
    briefs = "\n".join(
        f"<!-- prompt:{name} -->\n{text}\n<!-- end:{name} -->"
        for name, text in base.templates.items()
    )
    document: dict[str, object] = {
        "request_defaults": base.request_defaults.model_dump(),
        "values": dict(base.values),
        "texts": dict(base.texts),
        "phase_host": {"placement": "run_host", "max_units": 2},
    }
    overlay = ModelMorningOverlay.from_documents(document, briefs, base.phase_specs)
    assert overlay.phase_host.placement == "run_host"
    document["phase_host"] = {"placement": "run_host", "max_units": 9}
    with pytest.raises(ValueError, match="phase_host invalid"):
        ModelMorningOverlay.from_documents(document, briefs, base.phase_specs)


@pytest.mark.unit
async def test_run_host_serves_every_unit_on_the_runs_own_bus(
    runner: type[RecordingRunner],
) -> None:
    bus = EventBusInmemory(environment="dev", group="morning-run-host")
    await bus.start()
    try:
        gateway = HandlerMorningPhaseLabWork(bus, overlay=run_host_overlay())
        result = await gateway.run(request(TARGET), phase())
    finally:
        await bus.close()
    assert result == RESULT
    ((unit,),) = [runner.units]
    assert unit.target_host.startswith(RUN_HOST_PREFIX)
    assert runner.hosts == [unit.target_host]
    assert unit.argv[:3] == ["python3", AGENT_LAUNCHER, "opus"]
    assert unit.commit_sha == SHA


@pytest.mark.unit
async def test_run_host_failure_names_the_session_banner(
    runner: type[RecordingRunner],
) -> None:
    runner.exit_code = 1
    runner.tail = "You've hit your weekly limit · resets 9am"
    bus = EventBusInmemory(environment="dev", group="morning-run-host-fail")
    await bus.start()
    try:
        gateway = HandlerMorningPhaseLabWork(bus, overlay=run_host_overlay())
        with pytest.raises(MorningPhaseLabWorkError, match="weekly limit"):
            await gateway.run(request(TARGET), phase())
    finally:
        await bus.close()
    assert len(runner.units) == 1
