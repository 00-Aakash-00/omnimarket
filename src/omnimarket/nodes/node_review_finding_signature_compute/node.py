# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Declarative compute; the contract owns routing and the handler owns behavior."""

from omnibase_core.nodes.node_compute import NodeCompute

from omnimarket.models.model_review_finding_signature import (
    ModelReviewFindingSignature,
    ModelReviewFindingUncoded,
)


class NodeReviewFindingSignatureCompute(
    NodeCompute[ModelReviewFindingUncoded, ModelReviewFindingSignature]
):
    """Pure uncoded-finding signature compute node."""
