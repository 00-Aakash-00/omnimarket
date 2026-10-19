# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Fold signed findings and propose each recurrent near-duplicate group once."""

from __future__ import annotations

from collections.abc import MutableMapping
from datetime import timedelta
from pathlib import Path

import yaml

from omnimarket.models.model_review_finding_signature import (
    ModelReviewCodeCandidateProposed,
    ModelReviewFindingSignature,
    ModelReviewSignatureExample,
    ModelReviewSignatureGroup,
    ModelReviewSignaturePolicy,
    ModelReviewUncodedState,
)
from omnimarket.nodes.node_review_uncoded_signature_reducer.handlers.state_codec import (
    load_state,
    save_state,
)


def _matches(
    group: ModelReviewSignatureGroup,
    event: ModelReviewFindingSignature,
    threshold: float,
) -> bool:
    return group.family == event.family and any(
        member.signature == event.signature
        or sum(a == b for a, b in zip(member.minhash, event.minhash, strict=True))
        / len(event.minhash)
        >= threshold
        for member in group.representatives
    )


def fold(
    state: ModelReviewUncodedState,
    event: ModelReviewFindingSignature,
    policy: ModelReviewSignaturePolicy,
) -> tuple[ModelReviewUncodedState, ModelReviewCodeCandidateProposed | None]:
    """Pure delta. Event time never moves backward; PR/signature pairs count once."""
    if state.family != event.family:
        raise ValueError("finding family differs from the state partition")
    end = (
        max(state.watermark, event.observed_at)
        if state.watermark
        else event.observed_at
    )
    start = end - timedelta(days=policy.window_days)
    groups = [
        group.model_copy(
            update={
                "observations": tuple(
                    item
                    for item in group.observations
                    if start <= item.observed_at <= end
                )
            }
        )
        for group in state.groups
    ]
    if event.observed_at < start:
        return state.model_copy(
            update={"watermark": end, "groups": tuple(groups)}
        ), None

    matched = [
        group for group in groups if _matches(group, event, policy.similarity_threshold)
    ]
    remaining = [group for group in groups if group not in matched]
    representatives = {
        member.signature: member
        for group in matched
        for member in group.representatives
    }
    representatives.setdefault(event.signature, event)
    observations = {
        (item.repository.lower(), item.pr_number, item.signature): item
        for group in matched
        for item in group.observations
    }
    key = (event.repository.lower(), event.pr_number, event.signature)
    example = ModelReviewSignatureExample(
        signature=event.signature,
        repository=event.repository.lower(),
        pr_number=event.pr_number,
        finding_id=event.finding_id,
        observed_at=event.observed_at,
    )
    old = observations.get(key)
    if old is None or (example.observed_at, example.finding_id) > (
        old.observed_at,
        old.finding_id,
    ):
        observations[key] = example
    proposed = sorted(
        (group for group in matched if group.proposed_id),
        key=lambda group: group.group_id,
    )
    group_id = (
        proposed[0].group_id
        if proposed
        else min([group.group_id for group in matched] or [event.signature])
    )
    proposed_id = proposed[0].proposed_id if proposed else None
    examples = tuple(observations[key] for key in sorted(observations))
    pr_refs = tuple(
        sorted({f"{item.repository}#{item.pr_number}" for item in examples})
    )
    candidate = None
    if proposed_id is None and len(pr_refs) >= policy.distinct_prs:
        proposed_id = group_id
        candidate = ModelReviewCodeCandidateProposed(
            correlation_id=event.correlation_id,
            candidate_id=proposed_id,
            group_signature=group_id,
            family=event.family,
            content_tokens=representatives[min(representatives)].content_tokens,
            pr_refs=pr_refs,
            examples=examples,
            window_start=start,
            window_end=end,
        )
    remaining.append(
        ModelReviewSignatureGroup(
            group_id=group_id,
            family=event.family,
            representatives=tuple(
                representatives[key] for key in sorted(representatives)
            ),
            observations=examples,
            proposed_id=proposed_id,
        )
    )
    return ModelReviewUncodedState(
        family=state.family,
        watermark=end,
        groups=tuple(sorted(remaining, key=lambda group: group.group_id)),
    ), candidate


class HandlerReviewUncodedSignatureReducer:
    """Runtime state_io owns CAS and the in-row candidate outbox.

    An explicitly injected mapping is useful for deterministic previews. Normal
    bus dispatch requires the durable runtime seam and fails closed without it.
    """

    def __init__(
        self,
        states: MutableMapping[str, ModelReviewUncodedState] | None = None,
        policy: ModelReviewSignaturePolicy | None = None,
    ) -> None:
        self._states = states
        if policy is None:
            contract = yaml.safe_load(
                (Path(__file__).parents[1] / "contract.yaml").read_text()
            )
            policy = ModelReviewSignaturePolicy.model_validate(
                contract["recurrence_policy"]
            )
        self.policy = policy

    def handle(
        self, request: ModelReviewFindingSignature
    ) -> ModelReviewCodeCandidateProposed | None:
        prior = (
            self._states.get(request.family)
            if self._states is not None
            else load_state(request.family)
        )
        state, candidate = fold(
            prior or ModelReviewUncodedState(family=request.family),
            request,
            self.policy,
        )
        if self._states is not None:
            self._states[request.family] = state
        else:
            save_state(request.family, state)
        return candidate
