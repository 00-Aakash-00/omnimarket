# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""The experimental contract and all twelve chain documents are well typed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from omnibase_core.models.model_swap_validation import (
    ModelModelSwapValidationRequest,
    ModelModelSwapValidationResult,
)
from pydantic import ValidationError

_ROOT = Path(__file__).parents[4]
_CONTRACT = (
    _ROOT / "src/omnimarket/nodes/node_model_swap_validation_effect/contract.yaml"
)
_CHAINS = _ROOT / "tests/golden_chains/model_swap_validation"

pytestmark = pytest.mark.unit


def test_contract_declares_exactly_three_topics_and_analysis_only() -> None:
    contract = yaml.safe_load(_CONTRACT.read_text())
    topics = {
        *contract["event_bus"]["subscribe_topics"],
        *contract["event_bus"]["publish_topics"],
    }
    assert topics == {
        "onex.cmd.omnimarket.model-swap-validation-requested.v1",
        "onex.evt.omnimarket.model-swap-validation-completed.v1",
        "onex.evt.omnimarket.model-swap-validation-failed.v1",
    }
    assert set(contract["analysis_commands"]) == {"init", "report"}
    assert set(contract["analysis_commands"]).isdisjoint(
        {"apply", "rollback", "watch", "export", "corpus", "drift"}
    )
    assert len(contract["tool_pin"]["commit"]) == 40
    assert contract["descriptor"]["timeout_ms"] > 0
    assert contract["lifecycle"] == "experimental"


@pytest.mark.parametrize("source", sorted(_CHAINS.glob("*.json")), ids=lambda p: p.stem)
def test_all_chain_requests_and_expected_terminals_validate(source: Path) -> None:
    assert len(list(_CHAINS.glob("*.json"))) == 12
    document = json.loads(source.read_text())
    request = ModelModelSwapValidationRequest.model_validate(document["request"])
    result = ModelModelSwapValidationResult.model_validate(
        {
            "correlation_id": request.correlation_id,
            "quality_floor": request.quality_floor,
            "judge_mode": request.judge_mode,
            "tool_commit": yaml.safe_load(_CONTRACT.read_text())["tool_pin"]["commit"],
            "image_digest": "sha256:" + "a" * 64,
            **document["expected"],
        }
    )
    assert result.evidence_only is True


@pytest.mark.parametrize("floor", [0.8, 1.0])
def test_quality_floor_refuses_both_policy_bounds(floor: float) -> None:
    request = json.loads(next(_CHAINS.glob("*.json")).read_text())["request"]
    with pytest.raises(ValidationError):
        ModelModelSwapValidationRequest.model_validate(
            {**request, "quality_floor": floor}
        )
