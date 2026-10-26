# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Builders for release gate requests. Logins here are invented placeholders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from omnimarket.models.model_release_gate import (
    ModelReleaseGateIdentities,
    ModelReleaseGatePolicy,
    ModelReleaseGateRequest,
    ModelReleaseGrantRecord,
    ModelReleaseLabelEvent,
    ModelReleasePullRequestFacts,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
HEAD = "a" * 40
TRAIN = "onexbot-occ-writer[bot]"
POLICY_FILE = (
    Path(__file__).resolve().parents[3] / "config" / "release_gate_policy.yaml"
)

IDENTITIES = ModelReleaseGateIdentities(
    grantors={"operator": ("grantor-op",), "cloud_owner": ("grantor-co",)},
    agent_logins=("lab-bot", "pr-write-node"),
)


def load_policy() -> ModelReleaseGatePolicy:
    return ModelReleaseGatePolicy.model_validate(
        yaml.safe_load(POLICY_FILE.read_text())
    )


def label_event(
    actor: str = "grantor-op",
    *,
    label: str = "release-grant",
    actor_type: str = "User",
    via_app: bool = False,
    at: datetime = NOW - timedelta(minutes=5),
) -> ModelReleaseLabelEvent:
    return ModelReleaseLabelEvent(
        label=label,
        actor_login=actor,
        actor_type=actor_type,
        performed_via_app=via_app,
        created_at=at,
    )


def grant(**over: Any) -> ModelReleaseGrantRecord:
    base: dict[str, Any] = {
        "grant_id": "rg-0001",
        "repo": "OmniNode-ai/omnimarket",
        "pr_number": 77,
        "head_sha": HEAD,
        "requested_by": "lab-bot",
        "approved_by": "grantor-op",
        "expires_at": NOW + timedelta(hours=4),
        "source_ref": "main",
        "consumed": False,
    }
    base.update(over)
    return ModelReleaseGrantRecord(**base)


def pr(**over: Any) -> ModelReleasePullRequestFacts:
    base: dict[str, Any] = {
        "number": 77,
        "title": "chore: release omnimarket 0.5.0 (OMN-18595)",
        "head_ref": "automation/release-0.5.0-omn-18595",
        "head_sha": HEAD,
        "base_ref": "dev",
        "author_login": TRAIN,
        "author_type": "Bot",
        "labels": (),
        "version_changed": True,
        "head_push_actor_login": TRAIN,
        "commit_author_logins": (TRAIN,),
        "head_updated_at": NOW - timedelta(minutes=30),
        "label_events": (),
    }
    base.update(over)
    return ModelReleasePullRequestFacts(**base)


def request(
    policy: ModelReleaseGatePolicy,
    *,
    pull_request: ModelReleasePullRequestFacts | None = None,
    event: str = "pull_request",
    actor: str = TRAIN,
    actor_type: str = "Bot",
    tag: str = "",
    grants: tuple[ModelReleaseGrantRecord, ...] = (),
    identities: ModelReleaseGateIdentities = IDENTITIES,
) -> ModelReleaseGateRequest:
    return ModelReleaseGateRequest(
        repo="OmniNode-ai/omnimarket",
        event=event,
        actor_login=actor,
        actor_type=actor_type,
        now=NOW,
        policy=policy,
        identities=identities,
        pull_request=pull_request,
        tag=tag,
        grants=grants,
    )


def agent_release_pr(**over: Any) -> ModelReleasePullRequestFacts:
    """A release PR opened by an agent identity, not the train."""
    base: dict[str, Any] = {
        "title": "chore: release omnimarket 0.5.1",
        "head_ref": "OMN-1234-release",
        "author_login": "lab-bot",
        "author_type": "User",
        "head_push_actor_login": "lab-bot",
        "commit_author_logins": ("lab-bot",),
    }
    base.update(over)
    return pr(**base)


def granted_pr(**over: Any) -> ModelReleasePullRequestFacts:
    """An agent release PR that carries a valid label."""
    base: dict[str, Any] = {
        "labels": ("release-grant",),
        "label_events": (label_event(),),
    }
    base.update(over)
    return agent_release_pr(**base)
