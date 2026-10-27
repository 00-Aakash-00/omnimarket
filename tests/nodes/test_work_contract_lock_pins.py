"""Every work contract's uv.lock pin must equal the sha256 of the repo's uv.lock."""

import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
CONTRACTS = ROOT / "src/omnimarket/configs/work_contracts/contracts"


def _lock_pins(node: object) -> list[str]:
    if isinstance(node, dict):
        found = []
        if node.get("path") == "uv.lock" and "sha256" in node:
            found.append(str(node["sha256"]))
        for value in node.values():
            found.extend(_lock_pins(value))
        return found
    if isinstance(node, list):
        return [pin for item in node for pin in _lock_pins(item)]
    return []


def test_work_contract_uv_lock_pins_match_repo_lock() -> None:
    expected = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    pinned = {
        path.name: _lock_pins(yaml.safe_load(path.read_text()))
        for path in sorted(CONTRACTS.glob("*.yaml"))
    }
    pinned = {name: pins for name, pins in pinned.items() if pins}
    assert pinned, "no work contract pins uv.lock"
    stale = {
        name: [pin for pin in pins if pin != expected]
        for name, pins in pinned.items()
        if any(pin != expected for pin in pins)
    }
    assert not stale, f"stale uv.lock pins (expected {expected}): {sorted(stale)}"
