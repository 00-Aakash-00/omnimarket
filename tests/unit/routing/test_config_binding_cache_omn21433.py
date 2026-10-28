# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""A cached ladder follows the files that supply its backend placements."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from omnimarket.nodes.node_delegation_routing_reducer.handlers import (
    handler_delegation_routing as routing,
)

pytestmark = pytest.mark.unit

_CONFIGS = Path(__file__).resolve().parents[3] / "src" / "omnimarket" / "configs"
_PEER = "openrouter-north-mini-code"


@pytest.mark.parametrize(
    "binding_key",
    [
        "DELEGATION_ROUTING_TIERS_PATH",
        "BIFROST_CONTRACT_PATH",
        "BIFROST_OVERLAY_PATH",
    ],
)
def test_cached_ladder_changes_with_each_input_binding(
    binding_key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tiers = yaml.safe_load((_CONFIGS / "routing_tiers.yaml").read_text())
    bifrost = yaml.safe_load((_CONFIGS / "bifrost_delegation.yaml").read_text())
    peer = next(b for b in bifrost["backends"] if b["backend_id"] == _PEER)
    placement = peer.pop("placement")
    contract = tmp_path / "contract.yaml"
    contract.write_text(yaml.safe_dump(bifrost))
    monkeypatch.setenv("BIFROST_CONTRACT_PATH", str(contract))
    monkeypatch.setenv("BIFROST_OVERLAY_PATH", str(tmp_path / "absent.yaml"))
    monkeypatch.setenv(
        "DELEGATION_ROUTING_TIERS_PATH", str(_CONFIGS / "routing_tiers.yaml")
    )
    monkeypatch.setattr(routing, "_config", None)
    monkeypatch.setattr(routing, "_config_spread_peers", None)
    monkeypatch.setattr(routing, "_config_spread_weights", None)

    if binding_key == "DELEGATION_ROUTING_TIERS_PATH":
        before_data = tiers
        after_data = deepcopy(tiers)
        after_data["tiers"][0]["max_retries"] += 1
    elif binding_key == "BIFROST_CONTRACT_PATH":
        before_data = bifrost
        after_data = deepcopy(bifrost)
        next(b for b in after_data["backends"] if b["backend_id"] == _PEER)[
            "placement"
        ] = placement
    else:
        before_data = {"backends": []}
        after_data = {"backends": [{"backend_id": _PEER, "placement": placement}]}
    before_path = tmp_path / "before.yaml"
    after_path = tmp_path / "after.yaml"
    before_path.write_text(yaml.safe_dump(before_data))
    after_path.write_text(yaml.safe_dump(after_data))

    monkeypatch.setenv(binding_key, str(before_path))
    before = routing._get_config()
    assert routing._get_config() is before
    monkeypatch.setenv(binding_key, str(after_path))
    after = routing._get_config()
    assert after is not before
    assert routing._get_config() is after
    if binding_key == "DELEGATION_ROUTING_TIERS_PATH":
        assert after.tiers[0].max_retries == before.tiers[0].max_retries + 1
    else:
        before_free = next(t for t in before.tiers if t.name == "cheap_frontier")
        after_free = next(t for t in after.tiers if t.name == "cheap_frontier")
        assert not before_free.models
        assert [m.backend_ref for m in after_free.models] == [_PEER]

    # Restoring an older loaded object must retain its original provenance.
    monkeypatch.setattr(routing, "_config", before)
    assert routing._get_config() == after
    monkeypatch.setenv(binding_key, str(before_path))
    assert routing._get_config() == before
