# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Internal context-service roles keep their declared local route (OMN-21140)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from omnimarket.inference.task_class_authority import (
    EnumGatewayExposure,
    load_task_class_authority,
)
from omnimarket.models.delegation.wire.model_delegate_skill_request import (
    ModelDelegateSkillRequest,
)
from omnimarket.models.delegation.wire.model_delegation_request import (
    ModelDelegationRequest,
)
from omnimarket.nodes.node_delegation_routing_reducer.handlers import (
    handler_delegation_routing as routing,
)
from omnimarket.nodes.node_delegation_routing_reducer.models.model_delegation_config import (
    parse_delegation_config_yaml,
)

pytestmark = pytest.mark.unit

_CONFIGS = Path(__file__).resolve().parents[3] / "src/omnimarket/configs"
_CLASSES = ("extraction_proposer", "tail_summary", "context_selection")


@pytest.mark.parametrize("task_class", _CLASSES)
def test_context_role_contract_is_internal_and_local_only(task_class: str) -> None:
    contract = yaml.safe_load((_CONFIGS / "task_class_contracts.v1.yaml").read_text())
    entry = contract["task_classes"][task_class]
    authority = load_task_class_authority()

    assert (
        authority.task_classes[task_class].gateway_exposure
        is EnumGatewayExposure.INTERNAL
    )
    assert entry["selection"] == {"priority": 0, "phrases": []}
    assert entry["cloud_routing_policy"] == "blocked"
    assert entry["escalation_policy"] == {"max_escalations": 0, "tier_order": ["local"]}
    assert task_class not in contract.get("task_model_overrides", {})
    assert (
        entry["quality_gate"]
        == contract["task_classes"]["summarization"]["quality_gate"]
    )
    assert entry["latency_sla_p99_ms"] == 120000
    assert entry["definition_of_done"]["deterministic"] == ["response_non_empty"]
    # identifiers_grounded is declared in the quality gate node's contract, not
    # in quality_rules; summarization already arms it, so it is a known rule.
    known_rules = contract["quality_rules"].keys() | set(
        contract["task_classes"]["summarization"]["definition_of_done"]["heuristic"]
    )
    assert set(entry["definition_of_done"]["heuristic"]) <= known_rules
    assert "identifiers_grounded" in entry["definition_of_done"]["heuristic"]
    if task_class == "tail_summary":
        for field in (
            "complexity_contract",
            "output_contract",
            "required_capabilities",
        ):
            assert entry[field] == contract["task_classes"]["summarization"][field]
    else:
        assert entry["complexity_contract"] == {
            "output_kind": "structured",
            "verification": "exact_match",
            "execution_required": False,
        }
        assert entry["output_contract"] == {"output_shape": "json"}
    assert authority.execution_budget(task_class) == authority.execution_budget(
        "summarization"
    )


@pytest.mark.parametrize("task_class", _CLASSES)
@pytest.mark.parametrize("estimated_tokens", [0, 32768, 65536])
def test_context_role_resolves_through_routing_reducer(
    task_class: str, estimated_tokens: int
) -> None:
    config = parse_delegation_config_yaml((_CONFIGS / "routing_tiers.yaml").read_text())
    contract = yaml.safe_load((_CONFIGS / "task_class_contracts.v1.yaml").read_text())
    entry = routing._task_class_entry(contract, task_class)
    assert entry is not None
    tiers = routing._tier_order_from_contract(config, entry)
    assert [tier.name for tier in tiers] == ["local"]
    backends = {
        model.backend_ref: routing.BifrostBackendRef(
            endpoint_url="http://local-tier.invalid:8000/v1/chat/completions",
            model_name=model.id,
            timeout_ms=30000,
            max_tokens=model.max_context_tokens,
        )
        for tier in config.tiers
        for model in tier.models
    }
    selected = routing._select_model_for_task(
        tiers[0].models,
        task_class,
        estimated_tokens,
        backends,
        contract_model_ref=None,
    )
    assert selected is not None
    assert (tiers[0].name, selected.backend_ref, selected.id) == (
        "local",
        "local-coder",
        "Qwen3.8-27B",
    )


@pytest.mark.parametrize("task_class", _CLASSES)
def test_context_role_is_served_only_by_first_local_model(task_class: str) -> None:
    config = yaml.safe_load((_CONFIGS / "routing_tiers.yaml").read_text())
    local = config["tiers"][0]
    assert local["name"] == "local"
    assert local["models"][0]["max_context_tokens"] == 65536
    serving = [
        (tier["name"], model["backend_id"], model["id"])
        for tier in config["tiers"]
        for model in tier["models"]
        if task_class in model["use_for"]
    ]
    assert serving == [("local", "local-coder", "Qwen3.8-27B")]


@pytest.mark.parametrize("task_class", _CLASSES)
def test_both_wire_models_accept_context_role(task_class: str) -> None:
    payload = {
        "prompt": "Context-service role wire probe.",
        "task_type": task_class,
        "correlation_id": uuid4(),
    }
    skill = ModelDelegateSkillRequest.model_validate({**payload, "source": "codex"})
    delegation = ModelDelegationRequest.model_validate(
        {**payload, "emitted_at": datetime.now(UTC)}
    )
    assert skill.task_type == delegation.task_type == task_class
