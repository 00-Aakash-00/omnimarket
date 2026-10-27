# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Lane engine policy: RULING 2026-10-26T11:31:48Z, OMN-21396.

This module is dependency-free so private dispatch adapters can read the same
policy from their pinned omnimarket source without importing the runtime.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelLaneEnginePolicy:
    """One ceiling and ordered engines, with the reason for each choice."""

    claude_ceiling: str = "sonnet"
    engine_order: tuple[str, ...] = ("glm", "codex", "sonnet")
    reasons: tuple[str, ...] = (
        "Use GLM capacity first for eligible low-risk lanes.",
        "Use Codex for remaining work without spending Claude quota.",
        "Reserve Claude Sonnet for required workflows and exhausted alternatives.",
    )
    ruling: str = "2026-10-26T11:31:48Z"

    def cap(self, model: str) -> str:
        """An Opus request becomes Sonnet, including fully qualified model ids."""
        return self.claude_ceiling if "opus" in model.lower() else model


LANE_ENGINE_POLICY = ModelLaneEnginePolicy()
