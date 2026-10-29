# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Twelve plainly synthetic cases for adapter and contract-chain tests."""

from __future__ import annotations

import hashlib
from typing import Any

from omnimarket.nodes.node_model_swap_validation_effect.adapters.rightmodeler.replay_set import (
    canonical_json,
    message_key,
)


def golden_bundle() -> tuple[dict[str, Any], dict[str, bytes]]:
    records = [
        {
            "case_id": f"case-{i:02d}",
            "name": "fixture_task",
            "model": "example-reference/reference-model",
            "messages": [
                {"role": "system", "content": "Answer the public fixture question."},
                {"role": "user", "content": f"Synthetic public question {i}."},
            ],
            "response": {
                "choices": [
                    {"message": {"role": "assistant", "content": f"Reference {i}."}}
                ]
            },
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
        for i in range(1, 13)
    ]
    files = {
        "traces.jsonl": b"".join(canonical_json(record) + b"\n" for record in records),
        "candidate_answers.json": canonical_json(
            {
                message_key(record["messages"]): {
                    "content": f"Candidate {i}.",
                    "tokens_in": 10,
                    "tokens_out": 4,
                }
                for i, record in enumerate(records, 1)
            }
        ),
    }
    manifest = {
        "schema_version": 1,
        "source_type": "synthetic",
        "case_ids": [record["case_id"] for record in records],
        "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        },
        "provenance": {
            record["case_id"]: {
                "source": "Synthetic public fixture",
                "approved_external_judge": False,
            }
            for record in records
        },
    }
    return manifest, files
