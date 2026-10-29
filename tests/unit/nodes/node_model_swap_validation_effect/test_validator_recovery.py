# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Recovery fences a worker paused before it could launch any container."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler import (
    validator as validator_module,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.container import (
    TOOL_COMMIT,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.gateway import (
    ModelPrice,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.replay_set import (
    install_replay_set,
)
from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.validator import (
    ValidatorRightModeler,
)

from .golden_fixture import golden_bundle
from .test_container import _IMAGE, _MONITOR, _commands, _engine, _request


async def test_recovery_while_preflight_waits_cannot_later_start_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker, log = _engine(tmp_path / "engine")
    replay_root = tmp_path / "replay"
    manifest, files = golden_bundle()
    digest = install_replay_set(replay_root, manifest, files)
    req = _request()
    assert req.replay_set_digest == digest
    entered, release = threading.Event(), threading.Event()
    real_loader = validator_module.load_replay_set

    def delayed_loader(*args: Any, **kwargs: Any):
        entered.set()
        assert release.wait(timeout=5)
        return real_loader(*args, **kwargs)

    monkeypatch.setattr(validator_module, "load_replay_set", delayed_loader)
    adapter = ValidatorRightModeler(
        replay_root=replay_root,
        state_directory=tmp_path / "state",
        tool_pin=TOOL_COMMIT,
        image_digest=_IMAGE,
        prices={
            req.reference_model: ModelPrice(0.00001, 0.00003),
            req.candidate_model: ModelPrice(0.000001, 0.000003),
            "example-judge/neutral": ModelPrice(0.00002, 0.00006),
        },
        stub_judge_model="example-judge/neutral",
        runtime={"docker": docker, "monitor_image": _MONITOR},
    )
    pending = asyncio.create_task(adapter.validate(req))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        await adapter.recover(req.correlation_id)
    finally:
        release.set()
    result = await asyncio.wait_for(pending, timeout=5)
    assert result.failure_reason == "timeout"
    assert not any(
        command[0] in {"run", "create", "start"} or command[:2] == ["network", "create"]
        for command in _commands(log)
    )
