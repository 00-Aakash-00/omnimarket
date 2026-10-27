# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Authority fidelity verdict over real context-pack chunks."""

from pydantic import BaseModel, ConfigDict


class ModelContextPackFidelityVerdict(BaseModel):
    """Every expected authority item must survive whole under its original identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    violations: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Whether every authority item retained its exact original content."""
        return not self.violations
