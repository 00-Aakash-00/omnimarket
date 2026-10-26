# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Projection-applied notifications assemble captured compact content (TA.13)."""

from __future__ import annotations

import importlib
import tomllib
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from omnibase_infra.runtime.auto_wiring.discovery import discover_contracts_from_paths
from pydantic import ValidationError

from omnimarket.models.context_install_confirm import EnumInstallFact
from omnimarket.models.context_install_confirm_trigger import (
    EnumInstallConfirmTriggerOutcome,
    ModelInstallConfirmTriggerResult,
    ModelSessionCompactSnapshot,
    ModelSessionContentAppliedEvent,
)
from omnimarket.nodes.node_context_install_confirm_compute.handlers.handler_context_install_confirm import (
    HandlerContextInstallConfirm,
)
from omnimarket.nodes.node_context_install_confirm_trigger_effect.handlers.handler_context_install_confirm_trigger import (
    HandlerContextInstallConfirmTrigger,
)
from omnimarket.nodes.node_projection_compaction_receipts.handlers.handler_projection_compaction_receipts import (
    HandlerProjectionCompactionReceipts,
)
from omnimarket.nodes.node_projection_compaction_receipts.models import (
    ModelCompactionReceiptsProjectionRequest,
)
from tests.nodes.node_context_install_confirm_request_compute.test_handler_context_install_confirm_request import (
    CAPTURED,
    SESSION,
    activation,
    chunks,
    package,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
NODE_NAME = "node_context_install_confirm_trigger_effect"
NODE_DIR = ROOT / "src/omnimarket/nodes" / NODE_NAME


class FakeSessionCompactSource:
    """An in-memory source that checks the session selected by the trigger."""

    def __init__(self, snapshot: ModelSessionCompactSnapshot) -> None:
        self.snapshot = snapshot
        self.sessions_read: list[str] = []

    def read(self, session_id: str) -> ModelSessionCompactSnapshot:
        assert session_id == SESSION
        self.sessions_read.append(session_id)
        return self.snapshot


def test_custom_compaction_yields_request_with_records_and_activations() -> None:
    reader = FakeSessionCompactSource(
        ModelSessionCompactSnapshot(
            chunks=chunks(package(), 3), activations=(activation(),)
        )
    )
    result = HandlerContextInstallConfirmTrigger().handle(
        ModelSessionContentAppliedEvent(session_id=SESSION), reader
    )
    assert result.outcome is EnumInstallConfirmTriggerOutcome.REQUEST_READY
    assert result.request is not None
    assert result.request.records
    assert result.request.session_id == SESSION
    assert result.request.activations == reader.snapshot.activations
    assert result.request.newest_captured_at == CAPTURED
    assert result.packages_unreadable == ()
    assert reader.sessions_read == [SESSION]


def test_no_chunks_yields_no_compact_records_and_no_request() -> None:
    reader = FakeSessionCompactSource(
        ModelSessionCompactSnapshot(activations=(activation(),))
    )
    result = HandlerContextInstallConfirmTrigger().handle(
        ModelSessionContentAppliedEvent(session_id=SESSION), reader
    )
    assert result.outcome is EnumInstallConfirmTriggerOutcome.NO_COMPACT_RECORDS
    assert result.request is None
    assert result.packages_unreadable == ()
    assert reader.sessions_read == [SESSION]


def test_unreadable_only_package_yields_no_request() -> None:
    unreadable = chunks(package())[0].model_copy(update={"content_sha256": "0" * 64})
    reader = FakeSessionCompactSource(
        ModelSessionCompactSnapshot(chunks=(unreadable,), activations=(activation(),))
    )
    result = HandlerContextInstallConfirmTrigger().handle(
        ModelSessionContentAppliedEvent(session_id=SESSION), reader
    )
    assert result.outcome is EnumInstallConfirmTriggerOutcome.ALL_PACKAGES_UNREADABLE
    assert result.request is None
    assert result.packages_unreadable == ("0" * 64,)


def test_same_inputs_twice_yield_equal_results() -> None:
    reader = FakeSessionCompactSource(
        ModelSessionCompactSnapshot(
            chunks=chunks(package(), 3), activations=(activation(),)
        )
    )
    applied = ModelSessionContentAppliedEvent(session_id=SESSION)
    handler = HandlerContextInstallConfirmTrigger()
    assert handler.handle(applied, reader) == handler.handle(applied, reader)


def test_readable_and_unreadable_packages_keep_request_and_newest_capture() -> None:
    unreadable = chunks(package())[0].model_copy(
        update={
            "content_sha256": "0" * 64,
            "emitted_at": CAPTURED + timedelta(seconds=10),
        }
    )
    reader = FakeSessionCompactSource(
        ModelSessionCompactSnapshot(
            chunks=(*chunks(package(), 3), unreadable), activations=(activation(),)
        )
    )
    result = HandlerContextInstallConfirmTrigger().handle(
        ModelSessionContentAppliedEvent(session_id=SESSION), reader
    )
    assert result.outcome is EnumInstallConfirmTriggerOutcome.REQUEST_READY
    assert result.request is not None
    assert result.request.records
    assert result.request.newest_captured_at == unreadable.emitted_at
    assert result.packages_unreadable == ("0" * 64,)


def test_readable_empty_package_still_yields_request() -> None:
    reader = FakeSessionCompactSource(ModelSessionCompactSnapshot(chunks=chunks("")))
    result = HandlerContextInstallConfirmTrigger().handle(
        ModelSessionContentAppliedEvent(session_id=SESSION), reader
    )
    assert result.outcome is EnumInstallConfirmTriggerOutcome.REQUEST_READY
    assert result.request is not None
    assert result.request.records == ()
    assert result.packages_unreadable == ()


def test_wrong_session_in_snapshot_is_refused() -> None:
    wrong_session = chunks(package())[0].model_copy(update={"session_id": "other"})
    reader = FakeSessionCompactSource(
        ModelSessionCompactSnapshot(chunks=(wrong_session,))
    )
    with pytest.raises(ValueError, match="chunk session_id differs"):
        HandlerContextInstallConfirmTrigger().handle(
            ModelSessionContentAppliedEvent(session_id=SESSION), reader
        )


def test_models_are_frozen_and_forbid_extra_fields() -> None:
    for model in (
        ModelSessionContentAppliedEvent(session_id=SESSION),
        ModelSessionCompactSnapshot(),
        ModelInstallConfirmTriggerResult(
            outcome=EnumInstallConfirmTriggerOutcome.NO_COMPACT_RECORDS
        ),
    ):
        assert model.model_config["frozen"] is True
        with pytest.raises(ValidationError):
            type(model).model_validate(model.model_dump() | {"unexpected": True})
    with pytest.raises(ValidationError):
        ModelSessionContentAppliedEvent(session_id="")


def test_contract_metadata_handler_and_entry_point() -> None:
    path = NODE_DIR / "contract.yaml"
    contract = yaml.safe_load(path.read_text())
    discovery = discover_contracts_from_paths([path])
    assert not discovery.errors
    assert [node.name for node in discovery.contracts] == [NODE_NAME]
    assert contract["node_type"] == "effect"
    assert contract["descriptor"]["node_archetype"] == "effect"
    handler = contract["handler"]
    assert (
        getattr(importlib.import_module(handler["module"]), handler["class"])
        is HandlerContextInstallConfirmTrigger
    )
    assert contract["input_model"]["name"] == ModelSessionContentAppliedEvent.__name__
    assert contract["output_model"]["name"] == ModelInstallConfirmTriggerResult.__name__
    nodes = NODE_DIR.parent
    producer = yaml.safe_load(
        (nodes / "node_projection_session_content/contract.yaml").read_text()
    )
    consumer = yaml.safe_load(
        (nodes / "node_context_install_confirm_compute/contract.yaml").read_text()
    )
    applied = producer["terminal_event"]
    requested = consumer["runtime_dispatch"]["command_topic"]
    assert requested == "onex.cmd.omnimarket.context-install-confirm-requested.v1"
    assert applied in producer["event_bus"]["publish_topics"]
    assert contract["event_bus"]["subscribe_topics"] == [applied]
    assert contract["runtime_dispatch"]["command_topic"] == applied
    assert contract["event_bus"]["publish_topics"] == [requested]
    assert contract["terminal_event"] == requested
    assert contract["runtime_dispatch"]["terminal_events"]["success"] == requested
    assert contract["metadata"]["ticket"] == "OMN-21115"
    metadata = yaml.safe_load((NODE_DIR / "metadata.yaml").read_text())
    assert metadata["name"] == NODE_NAME
    assert metadata["tickets"] == ["OMN-21115"]
    entries = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "entry-points"
    ]["onex.nodes"]
    assert entries[NODE_NAME] == f"omnimarket.nodes.{NODE_NAME}"
    assert metadata["entry_points"]["onex.nodes"][NODE_NAME] == entries[NODE_NAME]


def test_trigger_request_feeds_install_confirmation_and_compaction_receipts() -> None:
    reader = FakeSessionCompactSource(
        ModelSessionCompactSnapshot(
            chunks=chunks(package(), 3), activations=(activation(),)
        )
    )
    triggered = HandlerContextInstallConfirmTrigger().handle(
        ModelSessionContentAppliedEvent(session_id=SESSION), reader
    )
    assert triggered.request is not None
    confirmed = HandlerContextInstallConfirm().handle(triggered.request)
    projected = HandlerProjectionCompactionReceipts().handle(
        ModelCompactionReceiptsProjectionRequest(event=confirmed)
    )
    (row,) = projected.rows
    assert row.verdict is EnumInstallFact.INSTALLED
    assert row.activation_id == activation().activation_id
