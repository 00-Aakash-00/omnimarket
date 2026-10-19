# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19448: a delegate-skill terminal's accepting attempt names delegation_events.routed_model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from omnimarket.nodes.node_projection_delegation.handlers.handler_delegation import (
    DelegationProjectionRunner,
)
from omnimarket.projection.protocol_database import InmemoryDatabaseAdapter
from omnimarket.projection.runner import MessageMeta
from omnimarket.projection.sqlite_database import SqliteDatabaseAdapter
from tests.helpers.tenant_registry import mock_tenant_registry
from tests.test_omn15905_delegation_projection_writer_seam import (
    _mock_db,
    _param_by_column,
)
from tests.unit.projection.test_delegation_backend_id_and_host_omn20162 import (
    _attempt,
    _project,
    _skill_terminal,
)

pytestmark = pytest.mark.unit


@pytest.fixture(params=["inmemory", "sqlite"])
def db(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "sqlite":
        return SqliteDatabaseAdapter(tmp_path / "local.db")
    return InmemoryDatabaseAdapter()


def test_accepting_attempt_model_id_reaches_routed_model(db: Any) -> None:
    refused = _attempt(
        backend_id="cloud-a",
        host=None,
        passed=False,
        failure_class="model_unavailable",
    )
    refused["model_id"] = "glm-5.3-flash"
    accepted = _attempt(backend_id="local-b", host="h202", passed=True)
    row = _project(_skill_terminal([refused, accepted]), db)
    assert row["routed_model"] == "qwen3.8-27b"


def test_ladder_without_accepted_rung_leaves_routed_model_null(db: Any) -> None:
    refused = _attempt(
        backend_id="cloud-a",
        host=None,
        passed=False,
        failure_class="model_unavailable",
    )
    row = _project(_skill_terminal([refused]), db)
    assert row.get("routed_model") is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_accepting_model_id_leaves_routed_model_null(db: Any, blank: str) -> None:
    accepted = _attempt(backend_id="local-b", host="h202", passed=True)
    accepted["model_id"] = blank
    row = _project(_skill_terminal([accepted]), db)
    assert row.get("routed_model") is None


def test_empty_ladder_leaves_routed_model_null(db: Any) -> None:
    row = _project(_skill_terminal([]), db)
    assert row.get("routed_model") is None


def test_sparse_skill_terminal_keeps_recorded_routed_model(db: Any) -> None:
    first = _skill_terminal([_attempt(backend_id="local-b", host="h202", passed=True)])
    _project(first, db)
    later = _skill_terminal([])
    later["correlation_id"] = first["correlation_id"]
    row = _project(later, db)
    assert row["routed_model"] == "qwen3.8-27b"


def test_skill_terminal_does_not_repurpose_answering_backend(db: Any) -> None:
    payload = _skill_terminal(
        [_attempt(backend_id="local-b", host="h202", passed=True)]
    )
    row = _project(payload, db)
    assert row.get("answering_backend") is None


async def test_async_runner_binds_skill_terminal_routed_model() -> None:
    runner = DelegationProjectionRunner()
    mock_db = _mock_db()
    mock_tenant_registry(mock_db)
    runner._db = mock_db
    accepted = _attempt(backend_id="local-b", host="h202", passed=True)
    accepted["model_id"] = "answering-model"
    payload = _skill_terminal([accepted])
    cid = payload["correlation_id"]
    assert await runner.project_event(
        runner._topic_delegate_skill_completed,
        payload,
        MessageMeta(partition=0, offset=0, fallback_id=cid),
    )
    (write,) = [
        call
        for call in mock_db.execute.await_args_list
        if str(call.args[0]).startswith("INSERT INTO delegation_events")
    ]
    stored = _param_by_column(write.args)
    assert stored["routed_model"] == "answering-model"
