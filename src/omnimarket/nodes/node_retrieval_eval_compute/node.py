# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""NodeRetrievalEvalCompute - contract-driven retrieval evaluation (OMN-21132, TC.9, AP-E1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from omnibase_core.nodes.node_compute import NodeCompute

from omnimarket.models.retrieval_eval import (
    ModelRetrievalEvalReport,
    ModelRetrievalEvalRequest,
)

if TYPE_CHECKING:
    from omnibase_core.models.container.model_onex_container import ModelONEXContainer


class NodeRetrievalEvalCompute(
    NodeCompute[ModelRetrievalEvalRequest, ModelRetrievalEvalReport]
):
    """Compute node: scores retrieval queries against contract-owned targets."""

    def __init__(self, container: ModelONEXContainer) -> None:
        super().__init__(container)


__all__ = ["NodeRetrievalEvalCompute"]
