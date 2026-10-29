# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Replay integrity is checked before any sandbox or gateway can start."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.replay_set import (
    ReplaySetError,
    canonical_json,
    install_replay_set,
    load_replay_set,
    message_key,
)

pytestmark = pytest.mark.unit


def bundle() -> tuple[dict, dict[str, bytes]]:
    messages = [
        {"role": "system", "content": "Summarize."},
        {"role": "user", "content": "Public example."},
    ]
    record = {
        "case_id": "case-001",
        "name": "summary",
        "model": "example-reference/model",
        "messages": messages,
        "response": {
            "choices": [{"message": {"role": "assistant", "content": "Example."}}]
        },
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    files = {
        "traces.jsonl": canonical_json(record) + b"\n",
        "candidate_answers.json": canonical_json(
            {
                message_key(messages): {
                    "content": "Example.",
                    "tokens_in": 10,
                    "tokens_out": 2,
                }
            }
        ),
    }
    manifest = {
        "schema_version": 1,
        "source_type": "synthetic",
        "case_ids": ["case-001"],
        "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        },
        "provenance": {
            "case-001": {
                "source": "synthetic public fixture",
                "approved_external_judge": False,
            }
        },
    }
    return manifest, files


def load(root: Path, digest: str):
    return load_replay_set(
        root,
        digest,
        task_type="summary",
        reference_model="example-reference/model",
        recorded=True,
    )


def test_atomic_install_and_repeated_load_preserve_bytes(tmp_path: Path) -> None:
    manifest, files = bundle()
    digest = install_replay_set(tmp_path, manifest, files)
    assert install_replay_set(tmp_path, manifest, files) == digest
    assert load(tmp_path, digest).files == load(tmp_path, digest).files == files
    assert digest == hashlib.sha256(canonical_json(manifest)).hexdigest()


@pytest.mark.parametrize(
    "filename", ["traces.jsonl", "candidate_answers.json", "manifest.json"]
)
def test_tampering_is_refused(tmp_path: Path, filename: str) -> None:
    manifest, files = bundle()
    digest = install_replay_set(tmp_path, manifest, files)
    target = tmp_path / digest / filename
    if filename == "manifest.json":
        manifest["provenance"]["case-001"]["approved_external_judge"] = True
        target.write_bytes(canonical_json(manifest))
    else:
        target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ReplaySetError):
        load(tmp_path, digest)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_key",
        "bool_usage",
        "missing_provenance",
        "wrong_answer_key",
        "duplicate_case",
        "wrong_task",
        "tool_call",
    ],
)
def test_invalid_bundle_never_loads(tmp_path: Path, mutation: str) -> None:
    manifest, files = bundle()
    record = json.loads(files["traces.jsonl"])
    if mutation == "extra_key":
        record["extra"] = 1
    elif mutation == "bool_usage":
        record["usage"]["prompt_tokens"] = True
    elif mutation == "missing_provenance":
        manifest["provenance"] = {}
    elif mutation == "wrong_answer_key":
        files["candidate_answers.json"] = canonical_json(
            {"0" * 64: {"content": "Example.", "tokens_in": 1, "tokens_out": 1}}
        )
    elif mutation == "wrong_task":
        record["name"] = "other"
    elif mutation == "tool_call":
        record["response"]["choices"][0]["message"]["tool_calls"] = [{"id": "tool"}]
    files["traces.jsonl"] = canonical_json(record) + b"\n"
    if mutation == "duplicate_case":
        files["traces.jsonl"] *= 2
    manifest["files"] = {
        name: hashlib.sha256(data).hexdigest() for name, data in files.items()
    }
    digest = install_replay_set(tmp_path, manifest, files)
    with pytest.raises(ReplaySetError):
        load(tmp_path, digest)


def test_interrupted_install_has_no_digest_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, files = bundle()

    def interrupted(*args):
        raise OSError("simulated power loss before rename")

    monkeypatch.setattr("os.rename", interrupted)
    with pytest.raises(OSError, match="simulated power loss"):
        install_replay_set(tmp_path, manifest, files)
    digest = hashlib.sha256(canonical_json(manifest)).hexdigest()
    with pytest.raises(ReplaySetError):
        load(tmp_path, digest)
    assert not list(tmp_path.iterdir())


def test_message_hash_matches_system_first_wire_encoding() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "system"},
    ]
    expected = hashlib.sha256(canonical_json([messages[1], messages[0]])).hexdigest()
    assert message_key(messages) == expected


@pytest.mark.parametrize(
    ("system", "expected"), [(["one", "two"], "one\ntwo"), ([""], None)]
)
def test_system_messages_merge_like_the_pinned_tool(system, expected) -> None:
    messages = [{"role": "system", "content": s} for s in system] + [
        {"role": "user", "content": "hi"}
    ]
    wire = ([{"role": "system", "content": expected}] if expected else []) + [
        {"role": "user", "content": "hi"}
    ]
    assert message_key(messages) == hashlib.sha256(canonical_json(wire)).hexdigest()


def test_unmanifested_file_and_symlink_refused(tmp_path: Path) -> None:
    manifest, files = bundle()
    digest = install_replay_set(tmp_path, manifest, files)
    (tmp_path / digest / "unexpected").write_text("injected")
    with pytest.raises(ReplaySetError):
        load(tmp_path, digest)
    (tmp_path / digest / "unexpected").unlink()
    traces = tmp_path / digest / "traces.jsonl"
    outside = tmp_path / "outside"
    traces.rename(outside)
    traces.symlink_to(outside)
    with pytest.raises(ReplaySetError):
        load(tmp_path, digest)
