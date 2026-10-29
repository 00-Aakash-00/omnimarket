# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Digest-addressed, atomically installed operator-owned replay bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ReplaySetError(ValueError):
    """A bundle is missing, incomplete, altered, or not text-replayable."""


def canonical_json(value: object) -> bytes:
    """Encode the manifest and message keys deterministically without NaN."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def wire_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Mirror the pinned trace adapter's merged, optional system message."""
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    return ([{"role": "system", "content": system}] if system else []) + [
        m for m in messages if m["role"] != "system"
    ]


def message_key(messages: list[dict[str, str]]) -> str:
    """Hash exactly the candidate message array sent by the pinned tool."""
    return hashlib.sha256(canonical_json(wire_messages(messages))).hexdigest()


@dataclass(frozen=True)
class ReplaySet:
    """Verified bytes; callers never reread mutable files after verification."""

    digest: str
    manifest: dict[str, Any]
    files: dict[str, bytes]
    cases: tuple[dict[str, Any], ...]
    candidate_answers: dict[str, dict[str, Any]]


def _record(value: object, task_type: str, reference_model: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReplaySetError("trace record must be an object")
    case_id = value.get("case_id", "unknown")
    if set(value) != {"case_id", "name", "model", "messages", "response", "usage"}:
        raise ReplaySetError(f"{case_id}: trace keys differ from the replay contract")
    if not isinstance(case_id, str) or not case_id:
        raise ReplaySetError("case_id must be a nonempty string")
    if value["name"] != task_type or value["model"] != reference_model:
        raise ReplaySetError(f"{case_id}: task or reference model does not match")
    messages = value["messages"]
    if not isinstance(messages, list) or not messages:
        raise ReplaySetError(f"{case_id}: messages must be nonempty")
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in {"system", "user", "assistant"}
            or not isinstance(message["content"], str)
        ):
            raise ReplaySetError(f"{case_id}: only text messages are replayable")
    usage = value["usage"]
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("prompt_tokens", "completion_tokens")
    ):
        raise ReplaySetError(f"{case_id}: recorded integer token usage is required")
    response = value["response"]
    try:
        choice = response["choices"][0]
        message = choice["message"]
        if not isinstance(message["content"], str) or message.get("tool_calls"):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ReplaySetError(
            f"{case_id}: reference response must contain plain text"
        ) from exc
    return value


def load_replay_set(
    root: Path,
    digest: str,
    *,
    task_type: str,
    reference_model: str,
    recorded: bool,
) -> ReplaySet:
    """Load only a complete digest address, verifying every byte and provenance."""
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ReplaySetError("invalid replay digest")
    directory = root / digest
    try:
        if directory.is_symlink() or not directory.is_dir():
            raise ReplaySetError("replay set is not installed")
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink():
            raise ReplaySetError("manifest symlinks are refused")
        manifest = json.loads(manifest_path.read_bytes())
        if hashlib.sha256(canonical_json(manifest)).hexdigest() != digest:
            raise ReplaySetError("manifest digest mismatch")
        if manifest["schema_version"] != 1 or not manifest["source_type"]:
            raise ReplaySetError("unsupported manifest or missing source")
        hashes = manifest["files"]
        if not isinstance(hashes, dict) or "traces.jsonl" not in hashes:
            raise ReplaySetError("trace file missing from manifest")
        files: dict[str, bytes] = {}
        for filename, expected in hashes.items():
            if Path(filename).name != filename or filename == "manifest.json":
                raise ReplaySetError("invalid bundle filename")
            source = directory / filename
            if source.is_symlink():
                raise ReplaySetError("bundle symlinks are refused")
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ReplaySetError(f"{filename}: content digest mismatch")
            files[filename] = data
        if {p.name for p in directory.iterdir()} != {*hashes, "manifest.json"}:
            raise ReplaySetError("unmanifested bundle files")
        cases = tuple(
            _record(json.loads(line), task_type, reference_model)
            for line in files["traces.jsonl"].splitlines()
            if line.strip()
        )
        ids = [case["case_id"] for case in cases]
        if not ids or len(ids) != len(set(ids)) or sorted(ids) != manifest["case_ids"]:
            raise ReplaySetError(
                "case ids must be unique and match the sorted manifest"
            )
        provenance = manifest["provenance"]
        if set(provenance) != set(ids):
            raise ReplaySetError("each case requires exactly one provenance entry")
        for case_id, entry in provenance.items():
            if (
                not isinstance(entry, dict)
                or type(entry.get("approved_external_judge")) is not bool
                or not isinstance(entry.get("source"), str)
                or not entry["source"]
            ):
                raise ReplaySetError(f"{case_id}: invalid provenance")
        answers = json.loads(files.get("candidate_answers.json", b"{}"))
        if not isinstance(answers, dict):
            raise ReplaySetError("candidate answers must be keyed objects")
        if recorded:
            keys = {message_key(case["messages"]) for case in cases}
            if set(answers) != keys:
                raise ReplaySetError(
                    "candidate answer keys do not match wire-message hashes"
                )
            for answer in answers.values():
                if (
                    not isinstance(answer, dict)
                    or not isinstance(answer.get("content"), str)
                    or any(
                        type(answer.get(k)) is not int or answer[k] < 0
                        for k in ("tokens_in", "tokens_out")
                    )
                ):
                    raise ReplaySetError(
                        "recorded answers require text and integer usage"
                    )
        return ReplaySet(digest, manifest, files, cases, answers)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ReplaySetError):
            raise
        raise ReplaySetError("replay set is missing or invalid") from exc


def install_replay_set(
    root: Path, manifest: dict[str, Any], files: dict[str, bytes]
) -> str:
    """Flush complete bytes before publishing a digest directory atomically."""
    digest = hashlib.sha256(canonical_json(manifest)).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=root))
    try:
        if set(manifest["files"]) != set(files):
            raise ReplaySetError("manifest file set does not match install")
        for filename, data in {
            **files,
            "manifest.json": canonical_json(manifest),
        }.items():
            if Path(filename).name != filename:
                raise ReplaySetError("invalid bundle filename")
            if (
                filename != "manifest.json"
                and hashlib.sha256(data).hexdigest() != manifest["files"][filename]
            ):
                raise ReplaySetError("install file digest mismatch")
            with (temporary / filename).open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.rename(temporary, root / digest)
        except OSError:
            if not (root / digest).is_dir():
                raise
            if any(
                (root / digest / name).read_bytes() != data
                for name, data in {
                    **files,
                    "manifest.json": canonical_json(manifest),
                }.items()
            ):
                raise ReplaySetError(
                    "existing digest address has different bytes"
                ) from None
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return digest
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
