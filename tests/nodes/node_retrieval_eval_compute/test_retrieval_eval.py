# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Retrieval metric regression tests (OMN-21132, plan task TC.9, port item AP-E1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from omnimarket.models.retrieval_eval import (
    EnumRetrievalQueryOutcome,
    ModelRetrievalEvalQuery,
    ModelRetrievalEvalRequest,
    ModelRetrievalEvalTargets,
)
from omnimarket.nodes.node_retrieval_eval_compute import HandlerRetrievalEval
from omnimarket.nodes.node_retrieval_eval_compute.handlers.handler_retrieval_eval import (
    bootstrap_interval,
    targets_from_contract,
)

pytestmark = pytest.mark.unit


def _contract() -> dict[str, Any]:
    contract_path = (
        Path(__file__).resolve().parents[3]
        / "src/omnimarket/nodes/node_retrieval_eval_compute/contract.yaml"
    )
    loaded: dict[str, Any] = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    return loaded


def _query(
    query_id: str = "q",
    slice_name: str = "profile",
    retrieved_ids: tuple[str, ...] = ("a",),
    relevant_ids: tuple[str, ...] = ("a",),
    outcome: EnumRetrievalQueryOutcome = EnumRetrievalQueryOutcome.OK,
) -> ModelRetrievalEvalQuery:
    return ModelRetrievalEvalQuery(
        query_id=query_id,
        slice_name=slice_name,
        retrieved_ids=retrieved_ids,
        relevant_ids=relevant_ids,
        outcome=outcome,
    )


def _request(
    *queries: ModelRetrievalEvalQuery,
    k_values: tuple[int, ...] = (10,),
    targets: ModelRetrievalEvalTargets | None = None,
) -> ModelRetrievalEvalRequest:
    return ModelRetrievalEvalRequest(
        queries=queries,
        k_values=k_values,
        targets=targets if targets is not None else targets_from_contract(_contract()),
    )


def test_fewer_than_k_retrieved_scores_recall_over_the_relevant_set() -> None:
    report = HandlerRetrievalEval().handle(
        _request(_query(relevant_ids=("a", "b")), k_values=(5, 10))
    )
    score = report.query_scores[0]
    assert score.recall_at_k[5] == 0.5
    assert score.recall_at_k[10] == 0.5
    assert score.precision_at_k[5] == 0.2


def test_failed_query_counts_in_the_denominator() -> None:
    report = HandlerRetrievalEval().handle(
        _request(
            _query(query_id="ok"),
            _query(
                query_id="failed",
                retrieved_ids=(),
                outcome=EnumRetrievalQueryOutcome.FAILED,
            ),
        )
    )
    for row in (report.overall, report.slices[0]):
        assert row.mean_recall_at_k[10] == 0.5
        assert row.mean_precision_at_k[10] == 0.05
        assert row.mean_average_precision == 0.5
        assert row.mean_reciprocal_rank == 0.5
        assert row.query_count == 2
        assert row.failed_query_count == 1
        assert row.recall_at_target_k_interval.mean == 0.5
    failed = report.query_scores[1]
    assert failed.outcome is EnumRetrievalQueryOutcome.FAILED
    assert failed.recall_at_k == {10: 0.0}
    assert failed.precision_at_k == {10: 0.0}
    assert failed.average_precision == 0.0
    assert failed.reciprocal_rank == 0.0


def test_average_precision_and_reciprocal_rank_golden() -> None:
    score = (
        HandlerRetrievalEval()
        .handle(
            _request(
                _query(relevant_ids=("a", "c"), retrieved_ids=("b", "a", "d", "c"))
            )
        )
        .query_scores[0]
    )
    assert score.average_precision == (1 / 2 + 2 / 4) / 2 == 0.5
    assert score.reciprocal_rank == 0.5


def test_average_precision_divides_by_the_relevant_set() -> None:
    score = (
        HandlerRetrievalEval()
        .handle(_request(_query(relevant_ids=("a", "b", "c"))))
        .query_scores[0]
    )
    assert score.average_precision == 1 / 3


def test_target_comes_from_the_contract() -> None:
    contract = _contract()
    contract_targets = targets_from_contract(contract)
    handler = HandlerRetrievalEval()
    assert contract_targets.target_k == contract["config"]["target_k"]["default"] == 10
    assert (
        contract_targets.min_recall_at_k
        == contract["config"]["min_recall_at_k"]["default"]
        == 0.8
    )
    request = _request(
        _query(relevant_ids=("a", "b")), k_values=(5, 1), targets=contract_targets
    )
    report = handler.handle(request)
    assert report.targets == request.targets
    assert request.targets.target_k in report.k_values
    assert report.k_values == (1, 5, 10)
    targets = ModelRetrievalEvalTargets(
        target_k=2,
        min_recall_at_k=0.4,
        interval_confidence=0.9,
        bootstrap_resamples=100,
        bootstrap_seed=7,
    )
    explicit = handler.handle(
        _request(*request.queries, k_values=request.k_values, targets=targets)
    )
    assert explicit.targets == targets
    assert explicit.k_values == (1, 2, 5)
    assert explicit.overall.meets_target is True
    assert report.overall.meets_target is False


def test_meets_target_is_judged_against_the_contract_target() -> None:
    relevant = tuple(str(i) for i in range(10))
    report = HandlerRetrievalEval().handle(
        _request(
            _query("above", "above", relevant[:9], relevant),
            _query("below", "below", relevant[:7], relevant),
            _query("boundary", "boundary", relevant[:8], relevant),
        )
    )
    assert [row.slice_name for row in report.slices] == ["above", "below", "boundary"]
    assert [row.meets_target for row in report.slices] == [True, False, True]
    assert report.slices[0].mean_recall_at_k[10] == 0.9
    assert report.slices[1].mean_recall_at_k[10] == 0.7
    assert report.slices[2].mean_recall_at_k[10] == 0.8


def test_interval_is_deterministic_between_runs() -> None:
    handler = HandlerRetrievalEval()
    request = _request(
        _query("q1", "mixed", relevant_ids=("a", "b")),
        _query("q2", "mixed"),
        _query("q3", "single", relevant_ids=("a", "b", "c")),
    )
    first = handler.handle(request)
    assert first == handler.handle(request)
    for row in (first.overall, *first.slices):
        interval = row.recall_at_target_k_interval
        assert interval.lower <= interval.mean <= interval.upper
        assert interval.mean == row.mean_recall_at_k[10]
        assert interval.confidence == request.targets.interval_confidence
        assert interval.method == "bootstrap-percentile"
        assert interval.resamples == request.targets.bootstrap_resamples
        assert interval.seed == request.targets.bootstrap_seed
    single = first.slices[1].recall_at_target_k_interval
    assert single.lower == single.mean == single.upper == 1 / 3


def test_slices_aggregate_separately_and_overall_spans_all() -> None:
    report = HandlerRetrievalEval().handle(
        _request(
            _query("z1", "z", relevant_ids=("a", "b")),
            _query("a1", "a"),
            _query(
                "z2", "z", retrieved_ids=(), outcome=EnumRetrievalQueryOutcome.FAILED
            ),
        )
    )
    assert [score.query_id for score in report.query_scores] == ["z1", "a1", "z2"]
    assert [row.slice_name for row in report.slices] == ["a", "z"]
    assert [row.query_count for row in report.slices] == [1, 2]
    assert [row.failed_query_count for row in report.slices] == [0, 1]
    assert [row.mean_recall_at_k[10] for row in report.slices] == [1.0, 0.25]
    assert report.overall.slice_name == "__all__"
    assert report.overall.query_count == 3
    assert report.overall.failed_query_count == 1
    assert report.overall.mean_recall_at_k[10] == 0.5
    assert report.overall.mean_average_precision == 0.5
    assert report.overall.mean_reciprocal_rank == 2 / 3


def test_slice_interval_does_not_depend_on_other_slices() -> None:
    handler = HandlerRetrievalEval()
    queries = (
        _query("z1", "z"),
        _query("z2", "z", retrieved_ids=()),
        _query("z3", "z", relevant_ids=("a", "b")),
    )
    alone = handler.handle(_request(*queries)).slices[0]
    together = handler.handle(_request(_query("a1", "a"), *queries)).slices[1]
    assert together == alone


def test_bootstrap_uses_floor_empirical_quantiles() -> None:
    targets = ModelRetrievalEvalTargets(
        target_k=10,
        min_recall_at_k=0.8,
        interval_confidence=0.34,
        bootstrap_resamples=100,
        bootstrap_seed=7,
    )
    interval = bootstrap_interval((0.0, 1.0), targets)
    # Seed 7 yields 33 zero means, 44 half means and 23 unit means.
    # floor(.33 * 99) = 32; floor(.67 * 99) = 66. Interpolation
    # at the lower quantile would incorrectly give a nonzero bound.
    assert interval.mean == 0.5
    assert interval.lower == 0.0
    assert interval.upper == 0.5


@pytest.mark.parametrize("retrieved_ids", [(), ("b", "c")])
def test_no_relevant_hits_scores_all_zeros(retrieved_ids: tuple[str, ...]) -> None:
    report = HandlerRetrievalEval().handle(
        _request(_query(retrieved_ids=retrieved_ids))
    )
    score = report.query_scores[0]
    assert score.recall_at_k == {10: 0.0}
    assert score.precision_at_k == {10: 0.0}
    assert score.average_precision == score.reciprocal_rank == 0.0
    assert report.overall.meets_target is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"relevant_ids": ()},
        {"relevant_ids": ("a", "a")},
        {"retrieved_ids": ("a", "a")},
        {"outcome": "failed", "retrieved_ids": ("a",)},
        {"query_id": ""},
        {"slice_name": ""},
        {"slice_name": "__all__"},
    ],
)
def test_invalid_queries_are_refused(overrides: dict[str, Any]) -> None:
    values = _query().model_dump()
    values.update(overrides)
    with pytest.raises(ValidationError):
        ModelRetrievalEvalQuery.model_validate(values)
    with pytest.raises(ValidationError, match="query_id"):
        _request(_query(), _query())
    with pytest.raises(ValidationError, match="k_values"):
        _request(_query(), k_values=(0,))


@pytest.mark.parametrize(
    "overrides",
    [
        {"queries": ()},
        {"k_values": ()},
        {"k_values": (1, 1)},
        {"target_k": 5},
        {"targets": ...},
    ],
)
def test_invalid_requests_are_refused(overrides: dict[str, Any]) -> None:
    values = _request(_query()).model_dump()
    for key, value in overrides.items():
        if value is Ellipsis:
            del values[key]
        else:
            values[key] = value
    with pytest.raises(ValidationError):
        ModelRetrievalEvalRequest.model_validate(values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_k": 0},
        {"min_recall_at_k": -0.1},
        {"min_recall_at_k": 1.1},
        {"interval_confidence": 0.0},
        {"interval_confidence": 1.0},
        {"bootstrap_resamples": 99},
    ],
)
def test_invalid_targets_are_refused(overrides: dict[str, Any]) -> None:
    values = targets_from_contract(_contract()).model_dump()
    values.update(overrides)
    with pytest.raises(ValidationError):
        ModelRetrievalEvalTargets.model_validate(values)


def test_missing_contract_key_raises() -> None:
    with pytest.raises(ValueError, match=r"config\.target_k\.default"):
        targets_from_contract({"config": {}})


@pytest.mark.parametrize("key", list(ModelRetrievalEvalTargets.model_fields))
def test_each_missing_contract_default_names_the_key(key: str) -> None:
    defaults = targets_from_contract(_contract()).model_dump()
    config = {name: {"default": value} for name, value in defaults.items()}
    del config[key]["default"]
    with pytest.raises(ValueError, match=key):
        targets_from_contract({"config": config})
