# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Measured roster selection and the contract-driven runtime seam (OMN-20910)."""

from __future__ import annotations

import importlib
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from omnimarket.nodes.node_review_roster_budget_compute import (
    NodeReviewRosterBudgetCompute,
)
from omnimarket.nodes.node_review_roster_budget_compute.handlers.handler_review_roster_budget import (
    HandlerReviewRosterBudget,
)
from omnimarket.nodes.node_review_roster_budget_compute.models.model_review_roster_budget import (
    ModelReviewRosterBudgetRequest,
    ModelReviewRosterBudgetResult,
)

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / "src/omnimarket/nodes/node_review_roster_budget_compute"


def _member(position: int, **changes: Any) -> dict[str, Any]:
    return {
        "voter_id": "sonnet",
        "persona_id": f"sonnet-{position}",
        "model_id": "claude-sonnet-5-5",
        "model_family": "sonnet",
        "persona_position": position,
        "tier": "required" if position == 1 else "budgeted",
        "mode": "inline",
        **changes,
    }


def _sample(position: int, mean: str, **changes: Any) -> dict[str, Any]:
    return {
        "model_id": "claude-sonnet-5-5",
        "persona_position": position,
        "billing_mode": "synchronous",
        "source": "live",
        "sample_size": 200,
        "mean_cost_usd": mean,
        **changes,
    }


@pytest.fixture
def payload() -> dict[str, Any]:
    lab = _member(
        1,
        voter_id="lab",
        persona_id="lab-1",
        model_id="lab-review-model",
        model_family="lab",
    )
    return {
        "candidate_rosters": [
            {
                "roster_id": f"R{n}",
                "members": [lab, *[_member(p) for p in range(1, n + 2)]],
                "worst_case_seconds": 1000 + n * 500,
            }
            for n in range(3)
        ],
        "cost_samples": [
            _sample(1, "0.0373"),
            _sample(2, "0.0200"),
            _sample(3, "0.0152"),
        ],
        "reviews_per_day": "312.7",
        "days_remaining": "30",
        "cycle_spend_usd": "0",
        "key_remaining_allowances_usd": ["190", "190", "190", "190"],
    }


def _run(payload: dict[str, Any]) -> ModelReviewRosterBudgetResult:
    return HandlerReviewRosterBudget().handle(
        ModelReviewRosterBudgetRequest.model_validate(payload)
    )


def test_richest_roster_under_680_uses_unrounded_cost(payload: dict[str, Any]) -> None:
    result = _run(payload)
    assert result.reason == "selected"
    assert result.selected_roster_id == "R1"
    assert result.working_budget_usd == Decimal("680")
    assert result.projected_reviews == Decimal("9381")
    assert [p.projected_cycle_cost_usd for p in result.projections] == [
        Decimal("349.9113"),
        Decimal("537.5313"),
        Decimal("680.1225"),
    ]
    assert result.projections[2].refusal_reasons == ("monthly_budget_exceeded",)


def test_current_run_rate_only_affords_floor(payload: dict[str, Any]) -> None:
    payload["reviews_per_day"] = "597"
    assert _run(payload).selected_roster_id == "R0"


def test_empty_sample_refuses_budgeted_persona(payload: dict[str, Any]) -> None:
    payload["cost_samples"][1]["sample_size"] = 0
    result = _run(payload)
    assert result.selected_roster_id == "R0"
    assert "insufficient_cost_sample" in result.projections[1].refusal_reasons
    assert result.projections[1].projected_cycle_cost_usd is None


def test_no_samples_never_assumes_free_api_calls(payload: dict[str, Any]) -> None:
    payload["cost_samples"] = []
    result = _run(payload)
    assert result.reason == "no_affordable_roster"
    assert result.selected_roster_id is None
    assert all(
        "insufficient_cost_sample" in p.refusal_reasons for p in result.projections
    )


def test_low_live_sample_uses_synchronous_replay(payload: dict[str, Any]) -> None:
    payload["cost_samples"][0]["sample_size"] = 199
    payload["cost_samples"].append(
        _sample(1, "0.038", source="replay", sample_size=300)
    )
    result = _run(payload)
    estimate = result.projections[0].estimates[0]
    assert estimate.source == "replay"
    assert estimate.sample_size == 300
    assert estimate.mean_cost_usd == Decimal("0.038")
    assert result.projections[0].projected_cycle_cost_usd == Decimal("356.478")


def test_200_live_reviews_take_precedence_over_replay(payload: dict[str, Any]) -> None:
    payload["cost_samples"].append(_sample(1, "1", source="replay", sample_size=300))
    assert _run(payload).projections[0].estimates[0].source == "live"


@pytest.mark.parametrize(
    ("billing_mode", "sample_size"), [("synchronous", 199), ("batch", 300)]
)
def test_inline_cannot_use_sparse_live_or_batch_prices(
    payload: dict[str, Any],
    billing_mode: str,
    sample_size: int,
) -> None:
    payload["cost_samples"][0].update(
        billing_mode=billing_mode, sample_size=sample_size
    )
    assert _run(payload).reason == "no_affordable_roster"


def test_advisory_uses_only_batch_costs(payload: dict[str, Any]) -> None:
    payload["candidate_rosters"] = payload["candidate_rosters"][:2]
    payload["candidate_rosters"][1]["members"][-1]["mode"] = "advisory"
    result = _run(payload)
    assert result.selected_roster_id == "R0"
    payload["cost_samples"].append(
        _sample(2, "0.01", billing_mode="batch", sample_size=1)
    )
    result = _run(payload)
    assert result.selected_roster_id == "R1"
    assert result.projections[1].estimates[-1].billing_mode == "batch"


@pytest.mark.parametrize(
    ("routing", "pricing"), [(False, False), (True, False), (False, True)]
)
def test_opus_refused_until_routing_and_per_model_pricing_land(
    payload: dict[str, Any],
    routing: bool,
    pricing: bool,
) -> None:
    payload["candidate_rosters"] = payload["candidate_rosters"][:2]
    payload["candidate_rosters"][1]["members"][-1] = _member(
        1,
        voter_id="opus",
        persona_id="opus-1",
        model_id="claude-opus-5-5",
        model_family="opus",
        tier="budgeted",
    )
    payload["cost_samples"].append(_sample(1, "0.001", model_id="claude-opus-5-5"))
    payload.update(
        opus_model_routing_ready=routing, opus_per_model_pricing_ready=pricing
    )
    result = _run(payload)
    assert result.selected_roster_id == "R0"
    assert "model_routing_unavailable" in result.projections[1].refusal_reasons
    payload.update(opus_model_routing_ready=True, opus_per_model_pricing_ready=True)
    assert _run(payload).selected_roster_id == "R1"


def test_r0_not_fitting_returns_no_affordable_roster(payload: dict[str, Any]) -> None:
    payload["cycle_spend_usd"] = "500"
    result = _run(payload)
    assert result.reason == "no_affordable_roster"
    assert result.selected_roster_id is None
    assert result.projections[0].projected_cycle_cost_usd == Decimal("849.9113")


def test_remaining_key_allowance_limits_future_cost(payload: dict[str, Any]) -> None:
    payload["key_remaining_allowances_usd"] = ["400"]
    assert _run(payload).selected_roster_id == "R0"
    payload["key_remaining_allowances_usd"] = []
    assert _run(payload).reason == "no_affordable_roster"
    assert "key_allowance_exceeded" in _run(payload).projections[0].refusal_reasons


def test_past_60_minute_job_budget_is_refused(payload: dict[str, Any]) -> None:
    payload["candidate_rosters"] = payload["candidate_rosters"][:2]
    payload["candidate_rosters"][1]["worst_case_seconds"] = 3601
    result = _run(payload)
    assert result.selected_roster_id == "R0"
    assert "job_budget_exceeded" in result.projections[1].refusal_reasons
    payload["candidate_rosters"][1]["worst_case_seconds"] = 3600
    assert _run(payload).selected_roster_id == "R1"


def test_exact_money_boundaries_are_allowed(payload: dict[str, Any]) -> None:
    payload.update(
        reviews_per_day="1",
        days_remaining="1",
        cycle_spend_usd="670",
        key_remaining_allowances_usd=["10"],
    )
    payload["cost_samples"] = [_sample(1, "10")]
    result = _run(payload)
    assert result.selected_roster_id == "R0"
    assert result.projections[0].projected_cycle_cost_usd == Decimal("680")


def test_richness_follows_configured_order_and_is_deterministic(
    payload: dict[str, Any],
) -> None:
    payload["candidate_rosters"][1]["roster_id"] = "R99"
    result = _run(payload)
    assert result.selected_roster_id == "R99"
    assert result == _run(payload)
    request = ModelReviewRosterBudgetRequest.model_validate(payload)
    assert NodeReviewRosterBudgetCompute().handle(request) == result
    assert (
        ModelReviewRosterBudgetResult.model_validate_json(result.model_dump_json())
        == result
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("monthly_budget_usd", "-1"),
        ("safety_factor", "1.01"),
        ("safety_factor", "0"),
        ("reviews_per_day", "-1"),
        ("days_remaining", "NaN"),
        ("cycle_spend_usd", "Infinity"),
        ("key_remaining_allowances_usd", ["-1"]),
        ("job_budget_seconds", 3601),
    ],
)
def test_invalid_budget_inputs_fail_closed(
    payload: dict[str, Any], field: str, value: Any
) -> None:
    payload[field] = value
    with pytest.raises(ValidationError):
        ModelReviewRosterBudgetRequest.model_validate(payload)


@pytest.mark.parametrize(
    "damage",
    [
        "no_floor",
        "drop_lab",
        "duplicate_roster",
        "duplicate_member",
        "duplicate_sample",
    ],
)
def test_invalid_candidates_fail_closed(payload: dict[str, Any], damage: str) -> None:
    if damage == "no_floor":
        payload["candidate_rosters"] = []
    elif damage == "drop_lab":
        payload["candidate_rosters"][1]["members"].pop(0)
    elif damage == "duplicate_roster":
        payload["candidate_rosters"][1]["roster_id"] = "R0"
    elif damage == "duplicate_member":
        payload["candidate_rosters"][1]["members"].append(_member(2))
    else:
        payload["cost_samples"].append(_sample(1, "1"))
    with pytest.raises(ValidationError):
        ModelReviewRosterBudgetRequest.model_validate(payload)


def test_contract_resolves_real_handler_and_bus_topics(payload: dict[str, Any]) -> None:
    contract = yaml.safe_load((NODE / "contract.yaml").read_text())
    handler = contract["handler"]
    cls = getattr(importlib.import_module(handler["module"]), handler["class"])
    request_ref = handler["input_model"]
    module, name = request_ref.rsplit(".", 1)
    request_cls = getattr(importlib.import_module(module), name)
    result = cls().handle(request_cls.model_validate(payload))
    assert result.selected_roster_id == "R1"
    dispatch = contract["runtime_dispatch"]
    assert dispatch["command_topic"] in contract["event_bus"]["subscribe_topics"]
    assert set(dispatch["terminal_events"].values()) == set(
        contract["event_bus"]["publish_topics"]
    )
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text())
    entry = manifest["project"]["entry-points"]["onex.nodes"][contract["name"]]
    assert (
        importlib.import_module(entry).NodeReviewRosterBudgetCompute
        is NodeReviewRosterBudgetCompute
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("spend", "expected"), [("0", "R1"), ("500", None)])
async def test_lab_runtime_dispatches_decision_over_declared_topics(
    payload: dict[str, Any],
    tmp_path: Path,
    spend: str,
    expected: str | None,
) -> None:
    from uuid import uuid4

    from omnimarket.adapters.codex.local_runtime_dispatch import LocalRuntimeDispatch
    from omnimarket.adapters.codex.runtime_client import ModelDispatchBusCommand
    from omnimarket.adapters.codex.topics import (
        TOPIC_CODEX_PATTERN_B_DISPATCH_COMMAND,
        TOPIC_CODEX_PATTERN_B_DISPATCH_COMPLETED,
    )

    payload["cycle_spend_usd"] = spend
    runtime = LocalRuntimeDispatch(
        adapter_command_topic=TOPIC_CODEX_PATTERN_B_DISPATCH_COMMAND,
        state_root=tmp_path / "runtime-state",
    )
    terminal, evidence = await runtime.dispatch(
        ModelDispatchBusCommand(
            command_name="review_roster_budget_compute",
            requester="pytest",
            payload=payload,
            correlation_id=uuid4(),
            timeout_seconds=5,
            response_topic=TOPIC_CODEX_PATTERN_B_DISPATCH_COMPLETED,
        )
    )
    contract = yaml.safe_load((NODE / "contract.yaml").read_text())
    assert terminal.status == "completed"
    assert terminal.payload is not None
    assert (
        ModelReviewRosterBudgetResult.model_validate(
            terminal.payload
        ).selected_roster_id
        == expected
    )
    assert terminal.payload["reason"] == (
        "selected" if expected else "no_affordable_roster"
    )
    assert evidence.command_topic == contract["runtime_dispatch"]["command_topic"]
    assert (
        evidence.terminal_topic
        == contract["runtime_dispatch"]["terminal_events"]["success"]
        == "onex.evt.omnimarket.review-roster-decided.v1"
    )
    assert evidence.runtime_observation.status == "OBSERVED"
    assert evidence.payload_model.endswith("ModelReviewRosterBudgetRequest")
    assert evidence.handler_route.endswith("HandlerReviewRosterBudget")


def test_budget_checks_are_wired_in_ci_and_precommit() -> None:
    command = "uv run pytest tests/test_golden_chain_review_roster_budget_compute.py -q -n 0 --no-cov"
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert any(step.get("run") == command for step in workflow["jobs"]["lint"]["steps"])
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hook = next(
        h
        for repo in config["repos"]
        for h in repo["hooks"]
        if h["id"] == "review-roster-budget"
    )
    assert hook["entry"] == command
    assert hook["pass_filenames"] is False
    assert hook["stages"] == ["pre-commit"]


@pytest.mark.asyncio
async def test_lab_runtime_rejects_invalid_typed_request(
    payload: dict[str, Any],
    tmp_path: Path,
) -> None:
    from uuid import uuid4

    from omnimarket.adapters.codex.local_runtime_dispatch import LocalRuntimeDispatch
    from omnimarket.adapters.codex.runtime_client import ModelDispatchBusCommand
    from omnimarket.adapters.codex.topics import (
        TOPIC_CODEX_PATTERN_B_DISPATCH_COMMAND,
        TOPIC_CODEX_PATTERN_B_DISPATCH_COMPLETED,
    )

    payload["candidate_rosters"] = []
    runtime = LocalRuntimeDispatch(
        adapter_command_topic=TOPIC_CODEX_PATTERN_B_DISPATCH_COMMAND,
        state_root=tmp_path / "runtime-state",
    )
    terminal, evidence = await runtime.dispatch(
        ModelDispatchBusCommand(
            command_name="review_roster_budget_compute",
            requester="pytest",
            payload=payload,
            correlation_id=uuid4(),
            timeout_seconds=5,
            response_topic=TOPIC_CODEX_PATTERN_B_DISPATCH_COMPLETED,
        )
    )
    assert terminal.status == "failed"
    assert "candidate_rosters" in str(terminal.error_message)
    assert evidence.runtime_observation.status == "UNOBSERVED"


@pytest.mark.parametrize("damage", ["lab_budgeted", "floor_budgeted", "extra_required"])
def test_declared_persona_roles_are_validated_by_typed_schemas(
    payload: dict[str, Any],
    damage: str,
) -> None:
    if damage == "lab_budgeted":
        payload["candidate_rosters"][0]["members"][0]["tier"] = "budgeted"
    elif damage == "floor_budgeted":
        payload["candidate_rosters"][0]["members"][1]["tier"] = "budgeted"
    else:
        payload["candidate_rosters"][1]["members"][-1]["tier"] = "required"
    with pytest.raises(ValidationError):
        ModelReviewRosterBudgetRequest.model_validate(payload)
