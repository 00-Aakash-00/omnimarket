# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Pinned, read-only analysis container with an independently owned firewall."""

from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from omnibase_core.models.model_swap_validation import ModelModelSwapValidationRequest

from .egress import capture_complete, firewall_script, violations

TOOL_COMMIT = "a1fed146ed738fd50be224716ad6139e35d6d173"
ANALYSIS_COMMANDS = frozenset({"init", "report"})

DOCKERFILE = """# Build only through build_pinned_image, which disables networking for every RUN.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS build
COPY tooling /tooling
COPY store /store
COPY source /source
COPY cache /prefetch-cache
COPY home /prefetch-home
WORKDIR /source
ENV PATH=/tooling/node_modules/.bin:/usr/local/bin:/usr/bin:/bin
ENV XDG_CACHE_HOME=/prefetch-cache HOME=/prefetch-home
RUN pnpm install --offline --frozen-lockfile --ignore-scripts --store-dir /store
RUN pnpm exec turbo run build --filter=@rightmodeler/cli
FROM ${BASE_IMAGE}
ARG TOOL_COMMIT
LABEL org.opencontainers.image.revision=${TOOL_COMMIT}
RUN git config --system --add safe.directory /synthetic
COPY --from=build /source/harness/packages/rightmodeler/dist-bundle/cli.js /opt/rightmodeler/cli.js
ENTRYPOINT []
CMD ["/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/state/home", "sleep", "infinity"]
"""


class SandboxError(RuntimeError):
    """The pinned sandbox or monitor could not be established."""


class SandboxCancelledError(SandboxError):
    """Recovery fenced this attempt before another resource could start."""


class EgressMonitorError(SandboxError):
    """The run cannot produce trustworthy final network evidence."""


@dataclass
class RunFence:
    """Serialize resource creation with recovery, without blocking on tool work."""

    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: Any = field(default_factory=threading.Lock)

    def check(self) -> None:
        if self.cancelled.is_set():
            raise SandboxCancelledError("validation ownership was fenced")


@dataclass(frozen=True)
class ToolRun:
    exit_code: int
    stdout: str
    stderr: str
    report: dict[str, Any] | None
    egress: str
    forbidden_destinations: tuple[str, ...]
    timed_out: bool = False


def build_pinned_image(
    context: Path,
    *,
    source_repository: Path,
    base_image: str,
    tool_commit: str = TOOL_COMMIT,
    docker: tuple[str, ...] = ("docker",),
) -> str:
    """Build archived source against an operator-prefetched, integrity-checked store.

    The preparation phase supplies context/store and context/tooling separately.
    This function fetches nothing, ignores every committed bundle, and prevents
    lifecycle scripts and build commands from using the network.
    """
    import io
    import tarfile

    if tool_commit != TOOL_COMMIT or "@sha256:" not in base_image:
        raise SandboxError("source and base image must match immutable pins")
    source = context / "source"
    if source.exists():
        raise SandboxError("build context source must be a fresh archive")
    archive = subprocess.run(
        ["git", "-C", str(source_repository), "archive", tool_commit],
        capture_output=True,
        check=True,
    ).stdout
    source.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        stream.extractall(source, filter="data")
    (context / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    (context / ".dockerignore").write_text(
        "source/**/node_modules\nsource/**/dist\nsource/**/dist-bundle\nsource/**/.git\n",
        encoding="utf-8",
    )
    subprocess.run(
        [*docker, "image", "inspect", base_image], capture_output=True, check=True
    )
    subprocess.run(
        [
            *docker,
            "build",
            "--pull=false",
            "--network=none",
            "--build-arg",
            f"BASE_IMAGE={base_image}",
            "--build-arg",
            f"TOOL_COMMIT={tool_commit}",
            "--iidfile",
            str(context / "image-id"),
            str(context),
        ],
        check=True,
    )
    return (context / "image-id").read_text().strip()


def analysis_argv(request: ModelModelSwapValidationRequest, base_url: str) -> list[str]:
    """All request variants produce exactly this analysis command."""
    return [
        "init",
        "--through",
        "report",
        "--yes",
        "--output",
        "json",
        "--repo",
        "/synthetic",
        "--store",
        "/state/store",
        "--traces",
        "/replay/traces.jsonl",
        "--policy",
        "/replay/policy.json",
        "--base-url",
        base_url,
        "--api-key-env",
        "RM_RUN_TOKEN",
        "--max-cost-usd",
        str(request.max_cost_usd),
        "--include-free",
    ]


def write_synthetic_repository(
    directory: Path, task_type: str, reference_model: str
) -> None:
    """The scanner reads this literal telemetry key; no application is executed."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "task.ts").write_text(
        'import { generateText } from "ai";\n'
        "export async function task() { return generateText({"
        f"model: {json.dumps(reference_model)}, prompt: 'synthetic scanner anchor', "
        f"experimental_telemetry: {{ functionId: {json.dumps(task_type)} }}"
        "}); }\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "--quiet", str(directory)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(directory), "add", "task.ts"], check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(directory),
            "-c",
            "user.name=Replay fixture",
            "-c",
            "user.email=replay@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "Synthetic scanner anchor",
        ],
        check=True,
        capture_output=True,
    )


class SandboxRunner:
    """No actual repository, provider key or writable host store enters the tool."""

    def __init__(
        self,
        *,
        image_digest: str,
        monitor_image: str,
        network: str,
        gateway_ip: str,
        gateway_port: int,
        docker: tuple[str, ...] = ("docker",),
        tool_commit: str = TOOL_COMMIT,
        timeout_seconds: float = 300,
        fence: RunFence | None = None,
    ) -> None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
            raise SandboxError("image must be addressed by immutable digest")
        if "@sha256:" not in monitor_image and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", monitor_image
        ):
            raise SandboxError("monitor image must be pinned")
        if tool_commit != TOOL_COMMIT:
            raise SandboxError(
                "unsupported tool pin; recapture output before upgrading"
            )
        firewall_script(gateway_ip, gateway_port)
        self.image_digest, self.monitor_image, self.network = (
            image_digest,
            monitor_image,
            network,
        )
        self.gateway_ip, self.gateway_port, self.docker = (
            gateway_ip,
            gateway_port,
            docker,
        )
        self.tool_commit, self.timeout_seconds = tool_commit, timeout_seconds
        self.fence = fence or RunFence()

    def _command(
        self, *argv: str, timeout: float = 30, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if argv[0] in {"run", "create", "start"}:
            with self.fence.lock:
                self.fence.check()
                return subprocess.run(
                    [*self.docker, *argv],
                    capture_output=True,
                    text=True,
                    check=check,
                    timeout=timeout,
                )
        return subprocess.run(
            [*self.docker, *argv],
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )

    def _execute(self, *argv: str, timeout: float) -> subprocess.CompletedProcess[str]:
        with self.fence.lock:
            self.fence.check()
            process = subprocess.Popen(
                [*self.docker, *argv],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        output: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
        size = 0
        deadline = time.monotonic() + timeout
        assert process.stdout is not None
        assert process.stderr is not None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(process.args, timeout)
                    for key, _ in selector.select(min(remaining, 0.2)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        size += len(chunk)
                        if size > 8_388_608:
                            process.kill()
                            process.wait()
                            return subprocess.CompletedProcess(
                                process.args,
                                process.returncode,
                                "",
                                '{"code":"tool_output_limit"}',
                            )
                        output[key.data].append(chunk)
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()
            process.stderr.close()
        return subprocess.CompletedProcess(
            process.args,
            process.returncode,
            b"".join(output["stdout"]).decode("utf-8", errors="replace"),
            b"".join(output["stderr"]).decode("utf-8", errors="replace"),
        )

    @staticmethod
    def names(correlation_id: UUID) -> tuple[str, str]:
        key = correlation_id.hex
        return f"rmv-{key}", f"rmv-net-{key}"

    def recover(self, correlation_id: UUID) -> None:
        """Fence and stop a prior deterministic container, without replacing it."""
        self.fence.cancelled.set()
        with self.fence.lock:
            for name in self.names(correlation_id):
                self._command("rm", "--force", name, check=False)

    def verify_image(self) -> None:
        actual = self._command(
            "image",
            "inspect",
            self.image_digest,
            "--format",
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        ).stdout.strip()
        if actual != self.tool_commit:
            raise SandboxError("image revision does not match tool pin")
        network = json.loads(self._command("network", "inspect", self.network).stdout)[
            0
        ]
        if network.get("Internal") is not True:
            raise SandboxError("sandbox network must be internal")

    def create_argv(
        self, correlation_id: UUID, replay: Path, synthetic: Path
    ) -> list[str]:
        name, monitor = self.names(correlation_id)
        return [
            "create",
            "--name",
            name,
            "--network",
            f"container:{monitor}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "1000:1000",
            "--pids-limit",
            "128",
            "--memory",
            "768m",
            "--cpus",
            "1",
            "--tmpfs",
            "/state:rw,nosuid,nodev,mode=1777,size=256m",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,mode=1777,size=32m",
            "--mount",
            f"type=bind,src={replay.resolve()},dst=/replay,readonly",
            "--mount",
            f"type=bind,src={synthetic.resolve()},dst=/synthetic,readonly",
            self.image_digest,
        ]

    def run(
        self,
        request: ModelModelSwapValidationRequest,
        replay: Path,
        synthetic: Path,
        token: str,
    ) -> ToolRun:
        self.verify_image()
        name, monitor = self.names(request.correlation_id)
        capture = ""
        run = subprocess.CompletedProcess(args=[], returncode=10, stdout="", stderr="")
        report = None
        timed_out = False
        deadline = time.monotonic() + self.timeout_seconds
        try:
            self._command(
                "run",
                "--detach",
                "--name",
                monitor,
                "--network",
                self.network,
                "--cap-add",
                "NET_ADMIN",
                "--cap-add",
                "NET_RAW",
                "--sysctl",
                "net.ipv6.conf.all.disable_ipv6=0",
                "--sysctl",
                "net.ipv6.conf.default.disable_ipv6=0",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=16m",
                self.monitor_image,
                "sleep",
                "infinity",
            )
            self._command(
                "exec",
                monitor,
                "sh",
                "-c",
                firewall_script(self.gateway_ip, self.gateway_port),
            )
            self._command(
                "exec",
                "--detach",
                monitor,
                "sh",
                "-c",
                "exec tcpdump -U -tt -n -l -i nflog:1 > /tmp/egress.log 2>&1",
            )
            # Readiness is observable: a missing capture interface refuses execution.
            for _ in range(30):
                capture = self._command(
                    "exec", monitor, "cat", "/tmp/egress.log", check=False
                ).stdout
                if "listening on nflog:1" in capture:
                    break
                time.sleep(0.1)
            else:
                raise EgressMonitorError("egress monitor did not become ready")
            self._command(*self.create_argv(request.correlation_id, replay, synthetic))
            self._command("start", name)
            environment = [
                "/usr/bin/env",
                "-i",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "HOME=/state/home",
                f"RM_RUN_TOKEN={token}",
                "node",
                "/opt/rightmodeler/cli.js",
            ]
            base_url = f"http://{self.gateway_ip}:{self.gateway_port}/v1"
            run = self._execute(
                "exec",
                name,
                *environment,
                *analysis_argv(request, base_url),
                timeout=max(0.1, deadline - time.monotonic()),
            )
            if run.returncode in {0, 1}:
                read = self._execute(
                    "exec",
                    name,
                    *environment,
                    "report",
                    "--repo",
                    "/synthetic",
                    "--store",
                    "/state/store",
                    "--output",
                    "json",
                    timeout=max(0.1, deadline - time.monotonic()),
                )
                if read.returncode in {0, 1}:
                    with suppress(json.JSONDecodeError):
                        report = json.loads(read.stdout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                # Freeze the tool first, then drain NFLOG before removing its namespace.
                self._command("pause", name, check=False)
                drained = self._command(
                    "exec",
                    monitor,
                    "sh",
                    "-c",
                    "pkill -INT tcpdump; i=0; while pgrep tcpdump >/dev/null && [ $i -lt 20 ]; do sleep 0.05; i=$((i+1)); done; cat /tmp/egress.log",
                    check=False,
                )
                if drained.returncode == 0 and capture_complete(drained.stdout):
                    capture = drained.stdout
                else:
                    raise EgressMonitorError("egress evidence could not be collected")
            finally:
                self.recover(request.correlation_id)
        return ToolRun(
            run.returncode,
            run.stdout,
            run.stderr,
            report,
            capture,
            violations(capture, self.gateway_ip, self.gateway_port),
            timed_out,
        )


class GatewayRelay:
    """Bridge the isolated tool to the authenticated gateway outside its namespace."""

    def __init__(
        self,
        *,
        docker: tuple[str, ...],
        monitor_image: str,
        correlation_id: UUID,
        host_address: str,
        host_port: int,
        fence: RunFence | None = None,
    ) -> None:
        if (
            not re.fullmatch(r"[a-zA-Z0-9.:-]+", host_address)
            or not 0 < host_port < 65536
        ):
            raise SandboxError("invalid operator gateway address")
        self.docker, self.image = docker, monitor_image
        self.name, self.network = (
            f"rmv-gateway-{correlation_id.hex}",
            f"rmv-network-{correlation_id.hex}",
        )
        self.host_address, self.host_port = host_address, host_port
        self.fence = fence or RunFence()

    def command(
        self, *argv: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if argv[0] == "run" or (
            argv[0] == "network" and argv[1] in {"create", "connect"}
        ):
            with self.fence.lock:
                self.fence.check()
                return subprocess.run(
                    [*self.docker, *argv],
                    check=check,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
        return subprocess.run(
            [*self.docker, *argv],
            check=check,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def start(self) -> str:
        # IPv6-enabled endpoints let denied IPv6 connects reach the logger,
        # rather than failing invisibly during route/source-address lookup.
        identifier = self.name.removeprefix("rmv-gateway-")
        subnet = f"fd{identifier[:2]}:{identifier[2:6]}:{identifier[6:10]}::/64"
        self.command(
            "network",
            "create",
            "--internal",
            "--ipv6",
            "--subnet",
            subnet,
            self.network,
        )
        self.command(
            "run",
            "--detach",
            "--name",
            self.name,
            "--network",
            "bridge",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            self.image,
            "socat",
            "TCP-LISTEN:8080,fork,reuseaddr",
            f"TCP:{self.host_address}:{self.host_port}",
        )
        self.command("network", "connect", self.network, self.name)
        data = json.loads(self.command("inspect", self.name).stdout)[0]
        return str(data["NetworkSettings"]["Networks"][self.network]["IPAddress"])

    def stop(self) -> None:
        self.command("rm", "--force", self.name, check=False)
        self.command("network", "rm", self.network, check=False)
