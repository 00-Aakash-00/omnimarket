# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-21433: committed gate files name roles, never a person's login."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omnimarket.models.model_release_gate import ModelReleaseGateIdentities

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def test_example_identities_file_lists_no_login() -> None:
    doc = yaml.safe_load(
        (ROOT / "config" / "release_gate_identities.example.yaml").read_text()
    )
    ids = ModelReleaseGateIdentities.model_validate(doc)
    assert all(not logins for logins in ids.grantors.values())
    assert ids.agent_logins == ()


def test_policy_roles_match_the_example_overlay_roles() -> None:
    policy = yaml.safe_load((ROOT / "config" / "release_gate_policy.yaml").read_text())
    example = yaml.safe_load(
        (ROOT / "config" / "release_gate_identities.example.yaml").read_text()
    )
    assert set(policy["grantor_roles"]) == set(example["grantors"])


def test_policy_train_login_is_an_app_not_a_person() -> None:
    policy = yaml.safe_load((ROOT / "config" / "release_gate_policy.yaml").read_text())
    assert all(login.endswith("[bot]") for login in policy["train_actor_logins"])
