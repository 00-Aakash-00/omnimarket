# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Exercise sandbox argv, capture draining and recovery at a process boundary.

The fake Docker executable records commands and simulates only the container
engine. Actual subprocess launch, timeout, cancellation locks and evidence
parsing remain production code. Real namespace enforcement is checked separately.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from omnibase_core.models.model_swap_validation import ModelModelSwapValidationRequest

from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.container import (
    TOOL_COMMIT,
    GatewayRelay,
    RunFence,
    SandboxCancelledError,
    SandboxError,
    SandboxRunner,
    analysis_argv,
)

_REQUEST = (
    Path(__file__).resolve().parents[3]
    / "golden_chains/model_swap_validation/chain_positive_recommend.json"
)
_IMAGE = "sha256:" + "b" * 64
_MONITOR = "example-monitor@sha256:" + "c" * 64
_CAPTURE = (
    "tcpdump: listening on nflog:1\n"
    "1700000000.1 IP 10.20.0.2.34567 > 10.20.0.1.8080: Flags [S]\n"
    "1700000000.2 IP 10.20.0.2.34568 > 203.0.113.7.443: Flags [S]\n"
    "2 packets captured\n0 packets received by filter\n0 packets dropped by kernel\n"
)
_FAKE_DOCKER = r"""
import json, pathlib, sys, time
config = json.loads(pathlib.Path(sys.argv[1]).read_text())
root = pathlib.Path(sys.argv[1]).parent
args = sys.argv[2:]
with (root / "commands.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\n")
if args[:2] == ["image", "inspect"]:
    print(config["tool_commit"])
elif args[:2] == ["network", "inspect"]:
    print(json.dumps([{"Internal": True}]))
elif args[0] == "inspect":
    print(json.dumps([{"NetworkSettings": {"Networks": {config["network"]: {"IPAddress": "10.20.0.1"}}}}]))
elif args[0] == "exec" and "wait-for-release" in args:
    (root / "executing").write_text("started")
    while not (root / "release").exists(): time.sleep(0.01)
elif args[0] == "exec" and any("pkill -INT tcpdump" in arg for arg in args):
    if config.get("drain_fail"): sys.exit(1)
    print(config["capture"])
elif args[0] == "exec" and "/tmp/egress.log" in args:
    print("tcpdump: listening on nflog:1")
elif args[0] == "exec" and "/opt/rightmodeler/cli.js" in args:
    if config.get("tool_output_bytes"):
        sys.stdout.write("x" * config["tool_output_bytes"])
        sys.stdout.flush()
        time.sleep(10)
    if config.get("tool_timeout"): time.sleep(10)
    print(json.dumps({"fixture": "report"}))
elif args[0] in ("run", "create", "start"):
    print("fixture-container")
"""


@pytest.mark.parametrize(
    "capture",
    [
        _CAPTURE.replace("0 packets dropped by kernel", "3 packets dropped by kernel"),
        _CAPTURE.rsplit("0 packets dropped by kernel", 1)[0],
        "tcpdump: pcap_loop: No space left on device\n" + _CAPTURE,
    ],
)
def test_incomplete_or_dropped_network_evidence_never_passes(capture: str) -> None:
    from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.egress import (
        capture_complete,
    )

    assert capture_complete(capture) is False


def _request() -> ModelModelSwapValidationRequest:
    return ModelModelSwapValidationRequest.model_validate(
        json.loads(_REQUEST.read_text())["request"]
    )


def test_untrusted_tool_output_is_bounded_before_the_process_deadline(
    tmp_path: Path,
) -> None:
    docker, _ = _engine(tmp_path, tool_output_bytes=9_175_040)
    runner = SandboxRunner(
        image_digest=_IMAGE,
        monitor_image=_MONITOR,
        network="fixture-network",
        gateway_ip="10.20.0.1",
        gateway_port=8080,
        docker=docker,
        timeout_seconds=3,
    )
    result = runner.run(_request(), tmp_path, tmp_path, "run-token")
    assert result.timed_out is False
    assert result.exit_code < 0
    assert json.loads(result.stderr)["code"] == "tool_output_limit"
    assert result.stdout == ""


def _engine(tmp_path: Path, **settings: Any) -> tuple[tuple[str, ...], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    script, config = tmp_path / "docker.py", tmp_path / "config.json"
    script.write_text(_FAKE_DOCKER)
    config.write_text(
        json.dumps(
            {
                "tool_commit": TOOL_COMMIT,
                "network": "fixture-net",
                "capture": _CAPTURE,
                **settings,
            }
        )
    )
    return (sys.executable, str(script), str(config)), tmp_path / "commands.jsonl"


def _runner(docker: tuple[str, ...], *, fence: RunFence | None = None) -> SandboxRunner:
    return SandboxRunner(
        image_digest=_IMAGE,
        monitor_image=_MONITOR,
        network="fixture-net",
        gateway_ip="10.20.0.1",
        gateway_port=8080,
        docker=docker,
        timeout_seconds=0.8,
        fence=fence,
    )


def _commands(log: Path) -> list[list[str]]:
    return (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )


@pytest.mark.parametrize("timed_out", [False, True])
def test_capture_is_drained_before_cleanup_and_preserves_forbidden_packets(
    tmp_path: Path, timed_out: bool
) -> None:
    docker, log = _engine(tmp_path, tool_timeout=timed_out)
    result = _runner(docker).run(
        _request(), tmp_path / "replay", tmp_path / "synthetic", "random-run-token"
    )
    assert result.timed_out is timed_out
    assert result.forbidden_destinations == ("203.0.113.7.443",)
    assert _CAPTURE in result.egress
    commands = _commands(log)
    pause = next(i for i, command in enumerate(commands) if command[0] == "pause")
    drain = next(
        i
        for i, command in enumerate(commands)
        if any("pkill -INT tcpdump" in arg for arg in command)
    )
    removals = [i for i, command in enumerate(commands) if command[0] == "rm"]
    assert pause < drain < min(removals)


@pytest.mark.parametrize("timed_out", [False, True])
def test_lost_final_egress_capture_fails_closed_even_after_timeout(
    tmp_path: Path, timed_out: bool
) -> None:
    docker, log = _engine(tmp_path, tool_timeout=timed_out, drain_fail=True)
    with pytest.raises(SandboxError, match="egress evidence"):
        _runner(docker).run(
            _request(), tmp_path / "replay", tmp_path / "synthetic", "run-token"
        )
    assert sum(command[0] == "rm" for command in _commands(log)) == 2


@pytest.mark.parametrize(
    "command", [("run", "container"), ("create", "container"), ("start", "container")]
)
def test_recovered_runner_cannot_launch_any_new_resource(
    tmp_path: Path, command: tuple[str, ...]
) -> None:
    docker, log = _engine(tmp_path)
    runner = _runner(docker)
    runner.recover(_request().correlation_id)
    before = _commands(log)
    with pytest.raises(SandboxCancelledError):
        runner._command(*command)
    with pytest.raises(SandboxCancelledError):
        runner._execute("exec", "container", "node", timeout=1)
    assert _commands(log) == before


def test_recovered_fence_also_blocks_relay_creation(tmp_path: Path) -> None:
    docker, log = _engine(tmp_path)
    fence = RunFence()
    _runner(docker, fence=fence).recover(_request().correlation_id)
    relay = GatewayRelay(
        docker=docker,
        monitor_image=_MONITOR,
        correlation_id=_request().correlation_id,
        host_address="127.0.0.1",
        host_port=8080,
        fence=fence,
    )
    before = _commands(log)
    with pytest.raises(SandboxCancelledError):
        relay.start()
    assert _commands(log) == before


def test_tool_wait_does_not_hold_the_recovery_lock(tmp_path: Path) -> None:
    docker, log = _engine(tmp_path)
    runner = _runner(docker)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            runner._execute, "exec", "container", "wait-for-release", timeout=5
        )
        try:
            deadline = time.monotonic() + 3
            while not (tmp_path / "executing").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (tmp_path / "executing").exists()
            assert runner.fence.lock.acquire(timeout=0.2), (
                "tool work must not block recovery ownership"
            )
            runner.fence.lock.release()
            runner.recover(_request().correlation_id)
            assert any(command[0] == "rm" for command in _commands(log))
        finally:
            (tmp_path / "release").touch()
        assert pending.result(timeout=5).returncode == 0


def test_runtime_argv_has_only_clean_environment_and_two_readonly_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROVIDER_KEY", "sentinel-provider-secret")
    docker, log = _engine(tmp_path)
    _runner(docker).run(
        _request(), tmp_path / "replay", tmp_path / "synthetic", "random-run-token"
    )
    commands = _commands(log)
    (create,) = [command for command in commands if command[0] == "create"]
    mounts = [create[i + 1] for i, token in enumerate(create) if token == "--mount"]
    assert len(mounts) == 2
    assert all(mount.endswith(",readonly") for mount in mounts)
    assert {mount.split("dst=")[1].split(",")[0] for mount in mounts} == {
        "/replay",
        "/synthetic",
    }
    for command in commands:
        if "/opt/rightmodeler/cli.js" not in command:
            continue
        clean = command.index("/usr/bin/env")
        node = command.index("node", clean)
        assert command[clean + 1] == "-i"
        assert {
            assignment.split("=", 1)[0] for assignment in command[clean + 2 : node]
        } == {"PATH", "HOME", "RM_RUN_TOKEN"}
    assert "sentinel-provider-secret" not in log.read_text()


@pytest.mark.parametrize(
    ("judge_mode", "answer_source"),
    [
        (judge, answer)
        for judge in ("stub", "backend")
        for answer in ("live", "recorded")
    ],
)
def test_every_request_variant_builds_only_the_analysis_command(
    judge_mode: str, answer_source: str
) -> None:
    payload = _request().model_dump(mode="json")
    payload.update(
        judge_mode=judge_mode,
        answer_source=answer_source,
        judge_backend_id="example-backend" if judge_mode == "backend" else None,
    )
    argv = analysis_argv(
        ModelModelSwapValidationRequest.model_validate(payload),
        "http://10.20.0.1:8080/v1",
    )
    assert argv[:4] == ["init", "--through", "report", "--yes"]
    assert set(argv).isdisjoint(
        {"apply", "rollback", "watch", "export", "corpus", "drift"}
    )
