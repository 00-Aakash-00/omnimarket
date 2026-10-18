# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""HandlerRetrievalEval: retrieval metrics (OMN-21132, plan task TC.9, port item AP-E1).

Pure and definition-B: handle(request) -> report. Recall divides by the relevant
set even for short retrievals; failed queries remain in every mean's denominator.
Targets come from the contract config block through the request; handle() does
no I/O, and all metric functions are pure.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from typing import Any

from omnimarket.models.retrieval_eval import (
    OVERALL_SLICE_NAME,
    EnumRetrievalQueryOutcome,
    ModelRetrievalEvalQuery,
    ModelRetrievalEvalReport,
    ModelRetrievalEvalRequest,
    ModelRetrievalEvalTargets,
    ModelRetrievalMetricInterval,
    ModelRetrievalQueryScore,
    ModelRetrievalSliceReport,
)


def score_query(
    query: ModelRetrievalEvalQuery, k_values: tuple[int, ...]
) -> ModelRetrievalQueryScore:
    """Score against all relevant ids, retaining zero rows for runner failures."""
    relevant = set(query.relevant_ids)
    retrieved = (
        query.retrieved_ids if query.outcome is EnumRetrievalQueryOutcome.OK else ()
    )
    hits_at_k = {k: len(set(retrieved[:k]) & relevant) for k in k_values}
    hits = 0
    precision_sum = 0.0
    reciprocal_rank = 0.0
    for rank, document_id in enumerate(retrieved, start=1):
        if document_id in relevant:
            hits += 1
            precision_sum += hits / rank
            if reciprocal_rank == 0.0:
                reciprocal_rank = 1.0 / rank
    return ModelRetrievalQueryScore(
        query_id=query.query_id,
        slice_name=query.slice_name,
        outcome=query.outcome,
        recall_at_k={k: hits_at_k[k] / len(relevant) for k in k_values},
        precision_at_k={k: hits_at_k[k] / k for k in k_values},
        average_precision=precision_sum / len(relevant),
        reciprocal_rank=reciprocal_rank,
    )


def bootstrap_interval(
    values: tuple[float, ...], targets: ModelRetrievalEvalTargets
) -> ModelRetrievalMetricInterval:
    """Bootstrap the mean with a fresh Random(seed) for each independent call.

    Each resample draws len(values) values with replacement. The empirical
    quantile of the sorted resampled means uses index floor(q * (n - 1)),
    where n is bootstrap_resamples; no interpolation or rounding is applied.
    """
    rng = random.Random(targets.bootstrap_seed)
    count = len(values)
    means = sorted(
        sum(rng.choices(values, k=count)) / count
        for _ in range(targets.bootstrap_resamples)
    )
    lower_q = (1.0 - targets.interval_confidence) / 2.0
    upper_q = (1.0 + targets.interval_confidence) / 2.0
    return ModelRetrievalMetricInterval(
        mean=sum(values) / count,
        lower=means[math.floor(lower_q * (len(means) - 1))],
        upper=means[math.floor(upper_q * (len(means) - 1))],
        confidence=targets.interval_confidence,
        method="bootstrap-percentile",
        resamples=targets.bootstrap_resamples,
        seed=targets.bootstrap_seed,
    )


def summarize_slice(
    slice_name: str,
    scores: tuple[ModelRetrievalQueryScore, ...],
    k_values: tuple[int, ...],
    targets: ModelRetrievalEvalTargets,
) -> ModelRetrievalSliceReport:
    """Average every query, including failures, and judge recall at target_k."""
    count = len(scores)
    recall = {
        k: sum(score.recall_at_k[k] for score in scores) / count for k in k_values
    }
    return ModelRetrievalSliceReport(
        slice_name=slice_name,
        query_count=count,
        failed_query_count=sum(
            score.outcome is EnumRetrievalQueryOutcome.FAILED for score in scores
        ),
        mean_recall_at_k=recall,
        mean_precision_at_k={
            k: sum(score.precision_at_k[k] for score in scores) / count
            for k in k_values
        },
        mean_average_precision=sum(score.average_precision for score in scores) / count,
        mean_reciprocal_rank=sum(score.reciprocal_rank for score in scores) / count,
        recall_at_target_k_interval=bootstrap_interval(
            tuple(score.recall_at_k[targets.target_k] for score in scores), targets
        ),
        meets_target=recall[targets.target_k] >= targets.min_recall_at_k,
    )


def targets_from_contract(contract: Mapping[str, Any]) -> ModelRetrievalEvalTargets:
    """Extract every config.<key>.default, naming any absent target key."""
    config = contract.get("config")
    defaults: dict[str, Any] = {}
    for key in ModelRetrievalEvalTargets.model_fields:
        if not isinstance(config, dict) or key not in config:
            raise ValueError(f"missing contract key: config.{key}.default")
        entry = config[key]
        if not isinstance(entry, dict) or "default" not in entry:
            raise ValueError(f"missing contract key: config.{key}.default")
        defaults[key] = entry["default"]
    return ModelRetrievalEvalTargets.model_validate(defaults)


class HandlerRetrievalEval:
    """Evaluate with contract config targets supplied through the request, without I/O."""

    def handle(self, request: ModelRetrievalEvalRequest) -> ModelRetrievalEvalReport:
        k_values = tuple(sorted({*request.k_values, request.targets.target_k}))
        scores = tuple(score_query(query, k_values) for query in request.queries)
        groups: dict[str, list[ModelRetrievalQueryScore]] = {}
        for score in scores:
            groups.setdefault(score.slice_name, []).append(score)
        return ModelRetrievalEvalReport(
            targets=request.targets,
            k_values=k_values,
            query_scores=scores,
            overall=summarize_slice(
                OVERALL_SLICE_NAME, scores, k_values, request.targets
            ),
            slices=tuple(
                summarize_slice(name, tuple(groups[name]), k_values, request.targets)
                for name in sorted(groups)
            ),
        )


__all__ = [
    "HandlerRetrievalEval",
    "bootstrap_interval",
    "score_query",
    "summarize_slice",
    "targets_from_contract",
]
