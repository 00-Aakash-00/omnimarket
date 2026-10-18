# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""TE.7 per-cell recall, cost, privacy and content-boundary controls."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from omnimarket.models.context_gate_cascade.model_context_gate_cascade_request import (
    ModelContextGateCascadeRequest,
)
from omnimarket.nodes.node_context_gate_cascade_compute.handlers import (
    HandlerContextGateCascade,
)

pytestmark = pytest.mark.unit


def payload() -> dict:
    return {
        "run_id": "run-1",
        "profile": "session_resume",
        "role": "selector",
        "gate": "kev-9b",
        "rung_id": "winning-selector",
        "winner_evidence_id": "upstream-verdict-1",
        "winner_passed": True,
        "work_unit_repository": "example/public-project",
        "repository_visibility": "public",
        "case_ids": ["relevant", "negative"],
        "trials": [
            {
                "case_id": case,
                "repeat": repeat,
                "relevant_ids": ["label-1"] if case == "relevant" else [],
                "ungated_recalled_ids": ["label-1"] if case == "relevant" else [],
                "cascade_recalled_ids": ["label-1"] if case == "relevant" else [],
                "gate_accepts": case == "relevant",
                "parse_valid": True,
                "ungated_cost_microusd": 100,
                "gate_cost_microusd": 10,
                "cascade_rung_cost_microusd": 100 if case == "relevant" else 0,
            }
            for case in ("relevant", "negative")
            for repeat in (1, 2, 3)
        ],
    }


def evaluate(data: dict):
    return HandlerContextGateCascade().handle(
        ModelContextGateCascadeRequest.model_validate(data)
    )


def test_ac1_preserved_recall_and_cost_saved_include_gate() -> None:
    result = evaluate(payload())
    assert result.recommended
    assert not result.reasons
    assert result.ungated_cost_microusd == 600
    assert result.cascade_cost_microusd == 360
    assert result.cost_saved_microusd == 240
    assert len(result.rows) == 6
    assert all(row.recall_equal for row in result.rows)


def test_ac1_one_repeat_loss_blocks_even_when_total_cost_is_lower() -> None:
    data = payload()
    data["trials"][1]["cascade_recalled_ids"] = []
    result = evaluate(data)
    assert not result.recommended
    assert "recall_mismatch" in result.reasons
    row = next(
        row for row in result.rows if row.case_id == "relevant" and row.repeat == 2
    )
    assert (row.ungated_hits, row.cascade_hits, row.relevant_count) == (1, 0, 1)


def test_ac1_aggregate_equality_cannot_hide_case_loss() -> None:
    data = payload()
    data["trials"][0]["cascade_recalled_ids"] = []
    for trial in data["trials"][3:]:
        trial["relevant_ids"] = ["label-2"]
        trial["gate_accepts"] = True
        trial["cascade_rung_cost_microusd"] = 20
    data["trials"][3]["cascade_recalled_ids"] = ["label-2"]
    result = evaluate(data)
    assert sum(row.ungated_hits for row in result.rows) == sum(
        row.cascade_hits for row in result.rows
    )
    assert not result.recommended


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown", "labels"])
def test_ac1_incomplete_or_inconsistent_case_matrix_is_refused(change: str) -> None:
    data = payload()
    if change == "missing":
        data["trials"].pop()
    elif change == "duplicate":
        data["trials"].append(data["trials"][0])
    elif change == "unknown":
        data["trials"][0]["case_id"] = "undeclared"
    else:
        data["trials"][0]["relevant_ids"] = ["changed-label"]
        data["trials"][0]["ungated_recalled_ids"] = []
        data["trials"][0]["cascade_recalled_ids"] = []
    with pytest.raises(ValidationError):
        evaluate(data)


@pytest.mark.parametrize("failure", ["cost", "parse", "winner"])
def test_ac1_no_recommendation_without_savings_valid_parse_and_winner(
    failure: str,
) -> None:
    data = payload()
    if failure == "cost":
        for trial in data["trials"]:
            trial["gate_cost_microusd"] = 100
    elif failure == "parse":
        data["trials"][0]["parse_valid"] = False
    else:
        data["winner_passed"] = False
    assert not evaluate(data).recommended


@pytest.mark.parametrize("visibility", ["private", "unknown"])
def test_ac2_jev_private_or_unverified_work_unit_is_refused(visibility: str) -> None:
    data = payload()
    data.update(gate="jev", repository_visibility=visibility)
    with pytest.raises(ValidationError, match="Jev requires public"):
        evaluate(data)


def test_ac2_jev_public_evidence_can_be_scored_without_a_model_call() -> None:
    data = payload()
    data["gate"] = "jev"
    result = evaluate(data)
    assert result.recommended
    assert result.gate_training_evidence_id is None


def test_ac3_gate_prose_is_rejected_and_result_has_no_package_surface() -> None:
    data = payload()
    data["trials"][0]["gate_output"] = "SENTINEL-DO-NOT-RENDER"
    with pytest.raises(ValidationError, match="Extra inputs"):
        evaluate(data)
    result = evaluate(payload()).model_dump(mode="json")
    assert not {"text", "package", "pack", "gate_output", "content"} & result.keys()
    assert "SENTINEL-DO-NOT-RENDER" not in str(result)


def test_rejected_segment_cannot_claim_extractor_results_or_cost() -> None:
    data = payload()
    data["trials"][0]["gate_accepts"] = False
    with pytest.raises(ValidationError, match="rejected"):
        evaluate(data)


def test_ac1_equal_hit_counts_cannot_trade_one_ruling_for_another() -> None:
    data = payload()
    for trial in data["trials"][:3]:
        trial["relevant_ids"] = ["label-1", "label-2"]
        trial["cascade_recalled_ids"] = ["label-2"]
    assert not evaluate(data).recommended


def test_ac1_empty_labels_do_not_supply_positive_recall() -> None:
    data = payload()
    for trial in data["trials"]:
        trial["relevant_ids"] = []
        trial["ungated_recalled_ids"] = []
        trial["cascade_recalled_ids"] = []
    result = evaluate(data)
    assert not result.recommended
    assert "no_positive_recall" in result.reasons
    assert all(not row.comparable for row in result.rows)


@pytest.mark.parametrize("gate", ["kev-9b", "kev-27b", "semif", "laya", "jev"])
def test_declared_gates_and_roles(gate: str) -> None:
    data = payload()
    data.update(
        gate=gate, role="extractor", gate_training_evidence_id="training-labels-1"
    )
    result = evaluate(data)
    assert result.recommended
    assert result.gate_training_evidence_id == "training-labels-1"


def test_laya_requires_training_evidence() -> None:
    data = payload()
    data["gate"] = "laya"
    with pytest.raises(ValidationError, match="Laya requires"):
        evaluate(data)


@pytest.mark.parametrize(
    "change", ["unknown-hit", "duplicate-label", "prose-decision", "negative-cost"]
)
def test_malformed_measurements_are_refused(change: str) -> None:
    data = payload()
    trial = data["trials"][0]
    if change == "unknown-hit":
        trial["cascade_recalled_ids"] = ["unlabelled"]
    elif change == "duplicate-label":
        trial["relevant_ids"].append("label-1")
    elif change == "prose-decision":
        trial["gate_accepts"] = "accept"
    else:
        trial["gate_cost_microusd"] = -1
    with pytest.raises(ValidationError):
        evaluate(data)


def test_recording_order_does_not_change_report() -> None:
    data = payload()
    expected = evaluate(data)
    data["trials"].reverse()
    assert evaluate(data).model_dump_json() == expected.model_dump_json()


@pytest.mark.parametrize("gate", ["kev-9b", "laya"])
def test_lab_runtime_adapter_bus_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    from omnimarket.adapters.codex.runtime_client import CodexRuntimeRequestAdapter

    monkeypatch.setenv("ONEX_LOCAL_RUNTIME_STATE_ROOT", str(tmp_path / "state"))
    data = payload()
    data["gate"] = gate
    if gate == "laya":
        data["gate_training_evidence_id"] = "training-labels-1"
    result = CodexRuntimeRequestAdapter(requester="cascade-test").dispatch_sync(
        command_name="node_context_gate_cascade_compute",
        payload=data,
        timeout_ms=5000,
        runtime_selection="local",
    )
    assert result.ok, result
    assert result.runtime_evidence is not None
    assert result.runtime_evidence.event_bus_backend == "inmemory"
    assert result.runtime_evidence.command_topic == (
        "onex.cmd.omnimarket.context-gate-cascade-requested.v1"
    )
    assert result.runtime_evidence.terminal_topic == (
        "onex.evt.omnimarket.context-gate-cascade-evaluated.v1"
    )
    assert result.output_payloads is not None
    assert len(result.output_payloads) == 1
    output = result.output_payloads[0]
    rows = output["rows"]
    assert len(rows) == 6
    assert all(row["recall_equal"] for row in rows)
    assert output == {
        "run_id": "run-1",
        "profile": "session_resume",
        "role": "selector",
        "gate": gate,
        "rung_id": "winning-selector",
        "winner_evidence_id": "upstream-verdict-1",
        **(
            {"gate_training_evidence_id": "training-labels-1"} if gate == "laya" else {}
        ),
        "work_unit_repository": "example/public-project",
        "repository_visibility": "public",
        "recommended": True,
        "reasons": [],
        "rows": rows,
        "ungated_cost_microusd": 600,
        "cascade_cost_microusd": 360,
        "cost_saved_microusd": 240,
    }


def test_registered_contract_resolves_to_typed_handler() -> None:
    from omnimarket.adapters.codex.local_runtime_dispatch import _resolve_node_route

    route = _resolve_node_route("node_context_gate_cascade_compute")
    assert route.handler_class == "HandlerContextGateCascade"
    assert route.input_model_name == "ModelContextGateCascadeRequest"
    assert route.command_topic.endswith(".context-gate-cascade-requested.v1")
    assert route.terminal_topic.endswith(".context-gate-cascade-evaluated.v1")
