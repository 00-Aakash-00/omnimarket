# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Wire types for the release gate (OMN-21433).

The gate decides one question from facts it is handed: is this pull request, tag
or dispatch a release, and if so is it the release train's or does it carry a
grant an agent cannot have made. Facts are gathered by the collector from the
GitHub API; nothing here reads a network.

Logins of people never appear in this repository. ``ModelReleaseGateIdentities``
is filled from a private overlay at run time; the committed policy names roles.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReleaseEvent = Literal["pull_request", "tag_push", "release_published", "dispatch"]

Verdict = Literal["NOT_RELEASE", "TRAIN", "GRANTED", "REFUSED"]


class ModelReleaseGatePolicy(BaseModel):
    """The committed, login-free half of the policy (config/release_gate_policy.yaml)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    release_tag_pattern: str
    release_title_prefixes: tuple[str, ...]
    release_labels: tuple[str, ...]
    release_branch_prefixes: tuple[str, ...]
    version_files: tuple[str, ...]
    grant_label: str
    grantor_roles: tuple[str, ...]
    train_actor_logins: tuple[str, ...]
    train_branch_prefixes: tuple[str, ...]
    grant_source_ref: str = "main"


class ModelReleaseGateIdentities(BaseModel):
    """The overlay half: logins by role. Absent overlay means no grantor exists."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grantors: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    agent_logins: tuple[str, ...] = ()


class ModelReleaseLabelEvent(BaseModel):
    """One ``labeled`` timeline event on the pull request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    actor_login: str
    actor_type: Literal["User", "Bot", "Organization", "Mannequin"] = "User"
    performed_via_app: bool = False
    created_at: datetime


class ModelReleasePullRequestFacts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    title: str
    head_ref: str
    head_sha: str = Field(min_length=7)
    base_ref: str
    author_login: str
    author_type: Literal["User", "Bot", "Organization", "Mannequin"] = "User"
    labels: tuple[str, ...] = ()
    version_changed: bool = False
    head_push_actor_login: str = ""
    commit_author_logins: tuple[str, ...] = ()
    head_updated_at: datetime | None = None
    label_events: tuple[ModelReleaseLabelEvent, ...] = ()


class ModelReleaseGrantRecord(BaseModel):
    """One entry of grants/release_grants.yaml, resolved at the policy's source ref.

    Same shape as the prod promotion grant (OMN-13418): ``approved_by`` differs
    from ``requested_by``, ``expires_at`` is absolute, and the record is bound to
    the exact thing granted (here repo, pull request number and head sha).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    grant_id: str
    repo: str
    pr_number: int = Field(ge=1)
    head_sha: str = Field(min_length=7)
    requested_by: str
    approved_by: str
    expires_at: datetime
    source_ref: str = "main"
    consumed: bool = False


class ModelReleaseGateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str
    event: ReleaseEvent
    actor_login: str
    actor_type: Literal["User", "Bot", "Organization", "Mannequin"] = "User"
    now: datetime
    policy: ModelReleaseGatePolicy
    identities: ModelReleaseGateIdentities = Field(
        default_factory=ModelReleaseGateIdentities
    )
    pull_request: ModelReleasePullRequestFacts | None = None
    tag: str = ""
    grants: tuple[ModelReleaseGrantRecord, ...] = ()


class ModelReleaseGateResult(BaseModel):
    """Typed verdict; ``allowed`` is the only field a check step needs to read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Verdict
    allowed: bool
    exit_code: Literal[0, 1]
    release_shape: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    grant_id: str = ""


__all__ = [
    "ModelReleaseGateIdentities",
    "ModelReleaseGatePolicy",
    "ModelReleaseGateRequest",
    "ModelReleaseGateResult",
    "ModelReleaseGrantRecord",
    "ModelReleaseLabelEvent",
    "ModelReleasePullRequestFacts",
    "ReleaseEvent",
    "Verdict",
]
