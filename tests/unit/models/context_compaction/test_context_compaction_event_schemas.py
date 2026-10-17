# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Compaction command and receipt event schemas (OMN-21112).

Two restricted commands and six receipt events, one typed model per topic. Each
model carries ``session_id`` (the partition key) and names its contracted
identity field in ``IDENTITY_FIELD``. The six receipts carry no free-text
content: every string the schema can hold is an id, a hash, an enum member or a
name, each bounded by a format, an enum or a pattern.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError

from omnimarket.events import topics
from omnimarket.models.context_compaction import (
    COMPACTION_COMMAND_MODELS,
    COMPACTION_RECEIPT_MODELS,
    ModelContextCompactionFallback,
    ModelContextCompactionInstallUncertain,
    ModelContextCompactionPackage,
    ModelContextCompactionPrepared,
    ModelContextCompactionRequested,
)

_COMMAND_TOPICS = (
    topics.CONTEXT_COMPACTION_REQUESTED_TOPIC_V1,
    topics.CONTEXT_COMPACTION_PACKAGE_TOPIC_V1,
)
_RECEIPT_TOPICS = (
    topics.CONTEXT_COMPACTION_PREPARED_TOPIC_V1,
    topics.CONTEXT_COMPACTION_ACTIVATED_TOPIC_V1,
    topics.CONTEXT_COMPACTION_FALLBACK_TOPIC_V1,
    topics.CONTEXT_COMPACTION_INSTALLED_TOPIC_V1,
    topics.CONTEXT_COMPACTION_INSTALL_MISMATCH_TOPIC_V1,
    topics.CONTEXT_COMPACTION_INSTALL_UNCERTAIN_TOPIC_V1,
)
_IDENTITIES = {
    "ModelContextCompactionRequested": "invocation_id",
    "ModelContextCompactionPackage": "invocation_id",
    "ModelContextCompactionPrepared": "candidate_id",
    "ModelContextCompactionActivated": "activation_id",
    "ModelContextCompactionFallback": "invocation_id",
    "ModelContextCompactionInstalled": "activation_id",
    "ModelContextCompactionInstallMismatch": "activation_id",
    "ModelContextCompactionInstallUncertain": "activation_id",
}
_ALL_MODELS = (*COMPACTION_COMMAND_MODELS, *COMPACTION_RECEIPT_MODELS)
_SHA = "a" * 64


def test_commands_are_restricted_and_receipts_are_one_topic_each() -> None:
    for topic in _COMMAND_TOPICS:
        assert topic.startswith("onex.cmd.omniintelligence.context-compaction-")
    for topic in _RECEIPT_TOPICS:
        assert topic.startswith("onex.evt.omnimarket.context-compaction-")
    assert len(set(_COMMAND_TOPICS + _RECEIPT_TOPICS)) == 8
    assert len(COMPACTION_COMMAND_MODELS) == len(_COMMAND_TOPICS)
    assert len(COMPACTION_RECEIPT_MODELS) == len(_RECEIPT_TOPICS)


@pytest.mark.parametrize("model", _ALL_MODELS, ids=lambda m: m.__name__)
def test_each_model_names_its_identity_and_partition_key(
    model: type[BaseModel],
) -> None:
    identity = vars(model)["IDENTITY_FIELD"]
    assert identity == _IDENTITIES[model.__name__]
    assert model.model_fields[identity].is_required()
    assert model.model_fields[identity].annotation is UUID
    assert model.model_fields["session_id"].is_required()


def _free_text_paths(schema: dict[str, Any]) -> list[str]:
    """Paths of string leaves bounded by neither a format, an enum nor a pattern."""
    defs: dict[str, Any] = schema.get("$defs", {})
    found: list[str] = []

    def walk(node: Any, path: str, seen: frozenset[str]) -> None:
        if not isinstance(node, dict):
            return
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            if name not in seen:
                walk(defs[name], path, seen | {name})
            return
        if node.get("type") == "string":
            if not ({"format", "enum", "pattern", "const"} & node.keys()):
                found.append(path)
            return
        for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            for index, child in enumerate(node.get(key, [])):
                walk(child, f"{path}[{key}{index}]", seen)
        if "items" in node:
            walk(node["items"], f"{path}[]", seen)
        for name, child in node.get("properties", {}).items():
            walk(child, f"{path}.{name}", seen)
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            walk(additional, f"{path}{{}}", seen)

    walk(schema, "", frozenset())
    return found


@pytest.mark.parametrize("model", COMPACTION_RECEIPT_MODELS, ids=lambda m: m.__name__)
def test_receipt_schema_holds_no_free_text_field(model: type[BaseModel]) -> None:
    assert _free_text_paths(model.model_json_schema()) == []


def test_free_text_detector_positive_control() -> None:
    class _Leaky(BaseModel):
        session_id: str
        note: str | None = None

    assert _free_text_paths(_Leaky.model_json_schema()) == [
        ".session_id",
        ".note[anyOf0]",
    ]


def test_the_package_reply_is_the_one_content_carrier() -> None:
    schema = ModelContextCompactionPackage.model_json_schema()
    assert _free_text_paths(schema) == [".messages[].text"]


@pytest.mark.parametrize("model", _ALL_MODELS, ids=lambda m: m.__name__)
def test_models_are_frozen_and_refuse_unknown_fields(model: type[BaseModel]) -> None:
    assert model.model_config.get("extra") == "forbid"
    assert model.model_config.get("frozen") is True


def test_receipts_refuse_a_free_text_value_in_a_bounded_field() -> None:
    with pytest.raises(ValidationError):
        ModelContextCompactionPrepared(
            candidate_id=uuid4(),
            session_id="s-1",
            input_messages_hash=_SHA,
            version_vector=(),
            version_vector_hash=_SHA,
            assembly_hash="the package summarised the session",
            factor_token_estimates=(),
            profile_name="compaction_orchestrator",
            profile_content_hash=_SHA,
            implementation_identity_hash=_SHA,
        )
    with pytest.raises(ValidationError):
        ModelContextCompactionFallback.model_validate(
            {
                "invocation_id": uuid4(),
                "session_id": "s-1",
                "failure_class": "the barrier timed out",
            }
        )


def test_not_installed_is_an_uncertain_reason_not_a_topic() -> None:
    receipt = ModelContextCompactionInstallUncertain.model_validate(
        {"activation_id": uuid4(), "session_id": "s-1", "reason": "native_compaction"}
    )
    assert receipt.boundary_uuid is None
    assert receipt.model_dump(mode="json")["reason"] == "native_compaction"


def test_request_barrier_marks_and_id_travel_together() -> None:
    base: dict[str, Any] = {
        "invocation_id": uuid4(),
        "session_id": "s-1",
        "cwd": "/work",
        "trigger": "manual",
        "input_messages_hash": _SHA,
    }
    ModelContextCompactionRequested(**base)
    with pytest.raises(ValidationError):
        ModelContextCompactionRequested(**base, session_barrier_id=uuid4())
    with pytest.raises(ValidationError):
        ModelContextCompactionRequested(
            **base,
            session_barrier_id=uuid4(),
            session_barrier=(
                {"stream": "lineage", "high_water": 3},
                {"stream": "lineage", "high_water": 4},
            ),
        )


def test_package_chunk_index_stays_below_its_count() -> None:
    with pytest.raises(ValidationError):
        ModelContextCompactionPackage(
            invocation_id=uuid4(),
            session_id="s-1",
            activation_id=uuid4(),
            installed_messages_hash=_SHA,
            chunk_index=1,
            chunk_count=1,
            messages=(),
        )
