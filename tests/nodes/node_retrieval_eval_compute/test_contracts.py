# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""node_retrieval_eval_compute declares its topics, resolves its handler and has no envelope (OMN-21132, plan task TC.9, port item AP-E1)."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from omnimarket.models.retrieval_eval import (
    ModelRetrievalEvalQuery,
    ModelRetrievalEvalRequest,
)
from omnimarket.nodes.node_retrieval_eval_compute import HandlerRetrievalEval
from omnimarket.nodes.node_retrieval_eval_compute.handlers.handler_retrieval_eval import (
    targets_from_contract,
)

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
NODE = "node_retrieval_eval_compute"
NODE_DIR = REPO / "src" / "omnimarket" / "nodes" / NODE
COMMAND = "onex.cmd.omnimarket.retrieval-eval-requested.v1"
EVENT = "onex.evt.omnimarket.retrieval-eval-scored.v1"


def _contract() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(
        (NODE_DIR / "contract.yaml").read_text(encoding="utf-8")
    )
    return loaded


def test_the_contract_declares_its_command_and_terminal_event() -> None:
    contract = _contract()
    assert contract["event_bus"]["subscribe_topics"] == [COMMAND]
    assert contract["event_bus"]["publish_topics"] == [EVENT]
    assert contract["runtime_dispatch"]["command_topic"] == COMMAND
    assert contract["terminal_event"] == EVENT


def test_the_contract_handler_resolves_to_a_class_with_handle() -> None:
    handler = _contract()["handler"]
    cls = getattr(importlib.import_module(handler["module"]), handler["class"])
    assert callable(cls.handle)
    assert _contract()["descriptor"]["purity"] == "pure"


def test_the_node_is_registered_as_an_onex_node_entry_point() -> None:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    entries = pyproject["project"]["entry-points"]["onex.nodes"]
    assert entries[NODE] == f"omnimarket.nodes.{NODE}"


def test_the_node_package_imports_no_event_envelope() -> None:
    """Definition-B: handle(request) -> result, no ModelEventEnvelope anywhere in the node."""
    sources = list(NODE_DIR.rglob("*.py"))
    assert sources
    for path in sources:
        assert "ModelEventEnvelope" not in path.read_text(encoding="utf-8"), path


def test_the_handler_hardcodes_no_topic_string() -> None:
    handler = NODE_DIR / "handlers" / "handler_retrieval_eval.py"
    assert "onex.cmd." not in handler.read_text(encoding="utf-8")
    assert "onex.evt." not in handler.read_text(encoding="utf-8")


def test_handler_module_reads_no_file() -> None:
    handler = NODE_DIR / "handlers" / "handler_retrieval_eval.py"
    source = handler.read_text(encoding="utf-8")
    for forbidden in ("read_text", "open(", "yaml", "node-purity-ok"):
        assert forbidden not in source


def test_contract_config_defaults_are_the_handler_and_report_targets() -> None:
    contract = _contract()
    defaults = {key: value["default"] for key, value in contract["config"].items()}
    assert defaults == {
        "target_k": 10,
        "min_recall_at_k": 0.8,
        "interval_confidence": 0.95,
        "bootstrap_resamples": 2000,
        "bootstrap_seed": 20261018,
    }
    targets = targets_from_contract(contract)
    assert targets.model_dump() == defaults
    report = HandlerRetrievalEval().handle(
        ModelRetrievalEvalRequest(
            queries=(
                ModelRetrievalEvalQuery(
                    query_id="q", slice_name="s", relevant_ids=("a",)
                ),
            ),
            k_values=(1,),
            targets=targets,
        )
    )
    assert report.targets.model_dump() == defaults
    assert report.targets == targets
