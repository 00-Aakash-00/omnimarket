# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Declarative reducer; runtime state_io supplies durable state and an outbox."""

from omnibase_core.nodes.node_reducer import NodeReducer

from omnimarket.models.model_review_finding_signature import (
    ModelReviewCodeCandidateProposed,
    ModelReviewFindingSignature,
)


class NodeReviewUncodedSignatureReducer(
    NodeReducer[ModelReviewFindingSignature, ModelReviewCodeCandidateProposed | None]
):
    """Family-partitioned uncoded recurrence reducer."""
