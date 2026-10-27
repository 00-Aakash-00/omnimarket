# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Failed work cannot replace a successful baseline run in the savings tracker."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from omnimarket.nodes.node_metering_summary_compute import (
    EnumMeteringMeasurement,
    HandlerMeteringSummary,
    ModelCounterfactualBaseline,
    ModelMeteringSummary,
    ModelMeteringSummaryRequest,
    ModelMeteringWindow,
    metering_record_from_row,
)
from omnimarket.nodes.node_projection_delegation.handlers.handler_run_savings import (
    modelled_run_savings_columns,
)
from omnimarket.nodes.node_projection_metering_summary import baseline as pricing

pytestmark = pytest.mark.unit

BASELINE = ModelCounterfactualBaseline(
    model="claude-sonnet-5-5",
    price_in_per_1k=Decimal("0.002"),
    price_out_per_1k=Decimal("0.01"),
    as_of="2026-10-06",
    pricing_manifest_version="1.0.0",
    source="test_fixture",
)


def row(cause: str | None, **changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "correlation_id": "failed-run",
        "created_at": "2026-10-25T12:00:00+00:00",
        "delegated_to": "Qwen3.8-27B",
        "model_name": "Qwen3.8-27B",
        "task_type": "document",
        "tokens_input": 1000,
        "tokens_output": 200,
        "cost_usd": "0.001",
        "cost_savings_usd": "0",
        "terminal_failure_cause": cause,
    }
    result.update(changes)
    return result


def summary(source: dict[str, object]) -> ModelMeteringSummary:
    record = metering_record_from_row(source)
    assert record is not None
    return HandlerMeteringSummary().handle(
        ModelMeteringSummaryRequest(
            records=(record,),
            window=ModelMeteringWindow(
                label="day", end=datetime(2026, 10, 26, tzinfo=UTC)
            ),
            baseline=BASELINE,
        )
    )


@pytest.mark.parametrize(
    "cause", ["timeout", "quality_gate_refused", "runtime_shutdown", "provider_error"]
)
def test_failed_work_has_zero_savings_and_keeps_measured_spend(cause: str) -> None:
    result = summary(row(cause))
    assert result.spend_usd == Decimal("0.001")
    assert result.savings_usd == Decimal("0")
    assert result.counterfactual_usd == result.spend_usd
    assert result.by_run[0].savings_usd == Decimal("0")
    assert result.by_model[0].savings_usd == Decimal("0")
    assert result.runs_measured == 1
    assert (result.tokens_in, result.tokens_out) == (1000, 200)


def test_success_still_uses_baseline_tokens_minus_spend() -> None:
    result = summary(row(None))
    assert result.counterfactual_usd == Decimal("0.004")
    assert result.savings_usd == Decimal("0.003")


@pytest.mark.parametrize(
    ("changes", "measurement"),
    [
        (
            {"tokens_input": 0, "tokens_output": 0},
            EnumMeteringMeasurement.UNKNOWN_TOKENS,
        ),
        ({"cost_usd": None}, EnumMeteringMeasurement.UNKNOWN_SPEND),
    ],
)
def test_failed_work_without_measurements_remains_unknown(
    changes: dict[str, object], measurement: EnumMeteringMeasurement
) -> None:
    result = summary(row("timeout", **changes))
    assert result.by_run[0].measurement is measurement
    assert result.savings_usd is None


@pytest.mark.parametrize(
    "cause", ["timeout", "quality_gate_refused", "runtime_shutdown"]
)
def test_the_dashboard_run_column_uses_the_same_failed_run_rule(
    cause: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pricing, "resolve_baseline", lambda _: BASELINE)
    columns = modelled_run_savings_columns(row(cause))
    assert Decimal(str(columns["modelled_savings_usd"])) == Decimal("0")
    assert columns["savings_measurement"] == "measured"
    assert columns["savings_baseline_model"] == BASELINE.model
