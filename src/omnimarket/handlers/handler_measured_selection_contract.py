# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Load selection inputs before calling the pure measured-selection node."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from omnimarket.models.delegation.model_measured_selection import (
    ModelMeasuredSelectionContract,
    ModelSelectionCandidate,
)
from omnimarket.nodes.node_measured_model_selection_compute.handlers.handler_measured_model_selection import (
    MeasuredSelectionContractError,
    candidates_from_routing_tiers,
)
from omnimarket.routing.routing_tiers_path import resolve_routing_tiers_path

CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "nodes/node_measured_model_selection_compute/contract.yaml"
)


def read_selection_contract(path: Path) -> ModelMeasuredSelectionContract:
    """Read the ``measured_selection`` block of a node contract file."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    block = raw.get("measured_selection") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        raise MeasuredSelectionContractError(
            f"{path} has no measured_selection mapping"
        )
    try:
        return ModelMeasuredSelectionContract.model_validate(block)
    except ValidationError as exc:
        raise MeasuredSelectionContractError(
            f"{path} measured_selection is mis-shaped: {exc}"
        ) from exc


@lru_cache(maxsize=1)
def load_measured_selection_contract() -> ModelMeasuredSelectionContract:
    """The shipped selection rules."""
    return read_selection_contract(CONTRACT_PATH)


def routing_contract_candidates() -> tuple[ModelSelectionCandidate, ...]:
    """The shipped rule applied to the routing contract in force."""
    routing_tiers: Any = yaml.safe_load(
        resolve_routing_tiers_path().read_text(encoding="utf-8")
    )
    return candidates_from_routing_tiers(
        load_measured_selection_contract().candidates, routing_tiers
    )
