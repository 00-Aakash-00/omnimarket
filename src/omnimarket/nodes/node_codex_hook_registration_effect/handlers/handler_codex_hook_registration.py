# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Merge the shipped registration into a Codex user hooks file (OMN-18759).

The Codex marketplace repoint is owned by Codex itself
(``codex plugin marketplace add <clone>/plugins``) and hook trust by Codex
(``/hooks`` in the TUI, or the app-server's ``hooks/list`` +
``config/batchWrite`` of ``hooks.state.<key>.trusted_hash``), so this node does
only the hooks.json merge. It never touches config.toml.

The registration is the four bus-mirror emitters declaring ``--actor codex``
(OMN-18704). Existing user hooks keep their groups, order and settings; only
missing commands from the shipped registration are appended.

The registration file is read at run time from the omniclaude plugin root passed in.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omnimarket.nodes.node_codex_hook_registration_effect.enums import (
    EnumCodexHookRegistrationStatus,
)
from omnimarket.nodes.node_codex_hook_registration_effect.models import (
    ModelCodexHookRegistrationRequest,
    ModelCodexHookRegistrationResult,
)


def merge_registration(
    registration: dict[str, Any], existing: dict[str, Any], plugin_root: Path
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Copy the user config and append only commands missing from each event."""
    merged = deepcopy(existing)
    events = merged.setdefault("hooks", {})
    added: list[str] = []
    for event, groups in registration["hooks"].items():
        target_groups = events.setdefault(event, [])
        present = {
            hook["command"]
            for group in target_groups
            for hook in group.get("hooks", [])
            if "command" in hook
        }
        for group in groups:
            rewritten = deepcopy(group)
            missing = []
            for hook in rewritten["hooks"]:
                command = f"CLAUDE_PLUGIN_ROOT={plugin_root} " + hook[
                    "command"
                ].replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
                if command not in present:
                    hook["command"] = command
                    missing.append(hook)
                    added.append(command)
                    present.add(command)
            if missing:
                rewritten["hooks"] = missing
                target_groups.append(rewritten)
    return merged, tuple(added)


def session_hook_overrides(plugin_root: Path, lane: str) -> list[str]:
    """Render capture-only session hooks; retain all existing user hooks and notify."""
    registration = _read_object(plugin_root / "hooks" / "codex" / "hooks.json")
    overrides = []
    for event, groups in registration["hooks"].items():
        captures = []
        for group in groups:
            for hook in group["hooks"]:
                # The prompt mirror owns turn allocation and already captures hook.event.
                script_name = (
                    "user_prompt_submit_bus_mirror.sh"
                    if event == "UserPromptSubmit"
                    else "claude_hook_capture.sh"
                )
                expected_command = (
                    "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/"
                    + script_name
                    + " --actor codex"
                )
                if hook.get("command") != expected_command:
                    continue
                script = plugin_root / "hooks" / "scripts" / script_name
                if not script.is_file():
                    raise ValueError("the capture script is missing")
                command = (
                    f"ONEX_LANE={shlex.quote(lane)} CLAUDE_PLUGIN_ROOT={shlex.quote(str(plugin_root))} "
                    f"bash {shlex.quote(str(script))} --actor codex"
                )
                captures.append(
                    '{matcher=".*",hooks=[{type="command",command='
                    + json.dumps(command)
                    + ",timeout=10}]}"
                )
        if captures:
            if not re.fullmatch(r"[A-Za-z]+", event):
                raise ValueError("invalid hook event name")
            overrides.append(f"hooks.{event}=[" + ",".join(captures) + "]")
    if len(overrides) != 9:
        raise ValueError("the native capture registration must cover nine events")
    return overrides


def discover_session_hook_trust(
    codex: str, cwd: Path, overrides: list[str]
) -> list[str]:
    """Ask the installed CLI for exact session-definition hashes; write no user config.

    Only this invocation's capture commands are trusted. No bypass flag, user hook,
    plugin hook, or persistent config mutation is involved. A discovery error refuses
    the lane before its first model call.
    """
    command = [codex, "app-server", "--listen", "stdio://"]
    for override in overrides:
        command.extend(("-c", override))
    expected = set()
    for override in overrides:
        # TOML's command string is JSON-compatible and generated above.
        match = re.search(r'command=("(?:[^"\\]|\\.)*")', override)
        if match:
            expected.add(json.loads(match.group(1)))
    requests = [
        {
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "onex-lane-hooks", "version": "1"}},
        },
        {"method": "initialized"},
        {"id": 2, "method": "hooks/list", "params": {"cwds": [str(cwd)]}},
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(
            ("\n".join(json.dumps(req) for req in requests) + "\n").encode()
        )
        process.stdin.flush()
        deadline, buffer = time.monotonic() + 20, b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(min(1, max(0, deadline - time.monotonic()))):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise ValueError("Codex hook discovery exited before its response")
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    response = json.loads(line)
                    if response.get("id") != 2:
                        continue
                    if "error" in response:
                        raise ValueError("Codex refused hooks/list")
                    trust = []
                    found_events = set()
                    for entry in response["result"]["data"]:
                        for hook in entry["hooks"]:
                            key, digest = hook["key"], hook["currentHash"]
                            if (
                                not key.startswith("/<session-flags>/config.toml:")
                                or hook.get("command") not in expected
                            ):
                                continue
                            if not re.fullmatch(
                                r"sha256:[0-9a-f]{64}", digest
                            ) or not hook.get("enabled"):
                                raise ValueError(
                                    "Codex returned an invalid or disabled capture definition"
                                )
                            found_events.add(hook.get("eventName"))
                            trust.append(
                                "hooks.state."
                                + json.dumps(key)
                                + ".trusted_hash="
                                + json.dumps(digest)
                            )
                    expected_events = {
                        item.split(".", 1)[1].split("=", 1)[0] for item in overrides
                    }
                    expected_events = {
                        event[0].lower() + event[1:] for event in expected_events
                    }
                    if len(trust) != len(overrides) or found_events != expected_events:
                        raise ValueError(
                            "Codex did not discover every session capture hook"
                        )
                    return trust
        raise ValueError("Codex hook discovery timed out")
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _read_object(path: Path) -> dict[str, Any]:
    """Read a JSON object without silently accepting another top-level shape."""
    document: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        message = f"{path}: expected a JSON object"
        raise ValueError(message)
    return document


class HandlerCodexHookRegistration:
    """Merge missing commands, optionally backing up and replacing the user file."""

    def handle(
        self, request: ModelCodexHookRegistrationRequest
    ) -> ModelCodexHookRegistrationResult:
        hooks_file = request.codex_home / "hooks.json"
        backup_path = ""

        def failed(exc: Exception) -> ModelCodexHookRegistrationResult:
            return ModelCodexHookRegistrationResult(
                status=EnumCodexHookRegistrationStatus.FAILED,
                exit_code=1,
                hooks_file=str(hooks_file),
                backup_path=backup_path,
                messages=(str(exc),),
            )

        try:
            registration = _read_object(
                request.plugin_root / "hooks" / "codex" / "hooks.json"
            )
            if not isinstance(registration.get("hooks"), dict):
                message = "registration must contain a hooks object"
                raise ValueError(message)
            try:
                existing = _read_object(hooks_file)
            except FileNotFoundError:
                existing = {}
            merged, added = merge_registration(
                registration, existing, request.plugin_root
            )
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            return failed(exc)

        if not added:
            status = EnumCodexHookRegistrationStatus.UNCHANGED
        elif not request.apply:
            status = EnumCodexHookRegistrationStatus.WOULD_ADD
        else:
            temporary: Path | None = None
            try:
                request.codex_home.mkdir(parents=True, exist_ok=True)
                if hooks_file.exists():
                    backup = (
                        request.codex_home
                        / f"hooks.json.bak.{request.now:%Y%m%dT%H%M%SZ}"
                    )
                    shutil.copy2(hooks_file, backup)
                    backup_path = str(backup)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=request.codex_home,
                    prefix=".hooks.json.",
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps(merged, indent=2) + "\n")
                os.replace(temporary, hooks_file)  # Atomic replacement.
            except OSError as exc:
                return failed(exc)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            status = EnumCodexHookRegistrationStatus.ADDED

        return ModelCodexHookRegistrationResult(
            status=status,
            exit_code=0,
            hooks_file=str(hooks_file),
            added_commands=added,
            backup_path=backup_path,
        )


def main(argv: list[str] | None = None) -> int:
    """Parse the filesystem operands and apply flag, then print the result as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", required=True, type=Path)
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser(),
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--session-config", type=Path)
    parser.add_argument("--lane")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    if args.session_config:
        if not args.lane or args.apply:
            parser.error("--session-config requires --lane and forbids --apply")
        try:
            overrides = session_hook_overrides(args.plugin_root, args.lane)
            trust = discover_session_hook_trust(args.codex, args.cwd, overrides)
            args.session_config.write_text(
                "\n".join(overrides + trust) + "\n", encoding="utf-8"
            )
        except (OSError, ValueError, KeyError):
            sys.stderr.write("Codex native hook configuration refused\n")
            return 1
        return 0
    result = HandlerCodexHookRegistration().handle(
        ModelCodexHookRegistrationRequest(
            plugin_root=args.plugin_root,
            codex_home=args.codex_home,
            apply=args.apply,
            now=datetime.now(UTC),
        )
    )
    sys.stdout.write(result.model_dump_json() + "\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
