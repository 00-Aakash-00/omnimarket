# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Pure release policy shared by the compute node and its JSON adapter.

Release-shaped changes belong to the train. Other pull requests need both a
person's label and a grant bound to the exact head; other release events refuse.
All facts, including the current time, come from the request.
"""

from __future__ import annotations

import re
from typing import Literal

from omnimarket.models.model_release_gate import (
    ModelReleaseGateRequest,
    ModelReleaseGateResult,
    ModelReleaseGrantRecord,
    ModelReleaseLabelEvent,
    ModelReleasePullRequestFacts,
)


def _label_reasons(
    event: ModelReleaseLabelEvent,
    pr: ModelReleasePullRequestFacts,
    agent_logins: set[str],
    grantors: set[str],
) -> list[str]:
    reasons: list[str] = []
    if event.actor_type != "User" or event.performed_via_app:
        reasons.append("label_actor_not_a_person")
    if event.actor_login not in grantors:
        reasons.append("label_actor_not_grantor")
    elif event.actor_login in agent_logins:
        reasons.append("label_actor_is_agent")
    if event.actor_login in (pr.author_login, pr.head_push_actor_login):
        reasons.append("label_actor_is_requester")
    if pr.head_updated_at is not None and event.created_at < pr.head_updated_at:
        reasons.append("label_older_than_head")
    return reasons


def _record_reasons(
    record: ModelReleaseGrantRecord,
    request: ModelReleaseGateRequest,
    agent_logins: set[str],
    grantors: set[str],
) -> list[str]:
    reasons: list[str] = []
    if record.source_ref != request.policy.grant_source_ref:
        reasons.append("grant_not_from_source_ref")
    if record.consumed:
        reasons.append("grant_consumed")
    if record.expires_at <= request.now:
        reasons.append("grant_expired")
    if record.approved_by == record.requested_by:
        reasons.append("self_granted")
    if grantors:
        if record.approved_by in agent_logins:
            reasons.append("approver_is_agent")
        if record.approved_by not in grantors:
            reasons.append("approver_not_grantor")
    return reasons


class HandlerReleaseGate:
    """Canonical compute handler for release train and human grant decisions."""

    @property
    def handler_type(self) -> Literal["NODE_HANDLER"]:
        return "NODE_HANDLER"

    @property
    def handler_category(self) -> Literal["COMPUTE"]:
        return "COMPUTE"

    def handle(self, request: ModelReleaseGateRequest) -> ModelReleaseGateResult:
        policy = request.policy
        pr = request.pull_request
        shape: list[str] = []
        if request.event == "pull_request":
            if pr is None:
                return ModelReleaseGateResult(
                    verdict="REFUSED",
                    allowed=False,
                    exit_code=1,
                    reasons=("pull_request_facts_missing",),
                )
            if pr.title.lower().startswith(policy.release_title_prefixes):
                shape.append("title")
            if any(label in policy.release_labels for label in pr.labels):
                shape.append("label")
            if pr.head_ref.startswith(policy.release_branch_prefixes):
                shape.append("branch")
            if pr.version_changed:
                shape.append("version")
        elif request.event == "tag_push":
            if re.match(policy.release_tag_pattern, request.tag):
                shape.append("tag")
        else:
            shape.append(request.event)

        release_shape = tuple(shape)
        if not release_shape:
            return ModelReleaseGateResult(
                verdict="NOT_RELEASE", allowed=True, exit_code=0
            )

        # A PR's sender can be a labeler or synchronizer, not its release author.
        train_logins = policy.train_actor_logins
        if request.event == "pull_request" and pr is not None:
            is_train = (
                pr.author_login in train_logins
                and pr.author_type == "Bot"
                and bool(pr.head_push_actor_login)
                and pr.head_push_actor_login in train_logins
                and bool(pr.commit_author_logins)
                and all(login in train_logins for login in pr.commit_author_logins)
                and pr.head_ref.startswith(policy.train_branch_prefixes)
            )
        else:
            is_train = (
                request.actor_login in train_logins and request.actor_type == "Bot"
            )
        if is_train:
            return ModelReleaseGateResult(
                verdict="TRAIN", allowed=True, exit_code=0, release_shape=release_shape
            )

        if request.event != "pull_request":
            reason = (
                "tag_actor_not_train"
                if request.event == "tag_push"
                else "release_actor_not_train"
            )
            return ModelReleaseGateResult(
                verdict="REFUSED",
                allowed=False,
                exit_code=1,
                release_shape=release_shape,
                reasons=(reason,),
            )

        assert pr is not None  # Missing PR facts already refused above.
        agent_logins = set(request.identities.agent_logins)
        grantors = {
            login
            for role in policy.grantor_roles
            for login in request.identities.grantors.get(role, ())
        }
        reasons: list[str] = []
        if not grantors:
            reasons.append("grantors_unavailable")

        label_events = [
            event for event in pr.label_events if event.label == policy.grant_label
        ]
        if policy.grant_label not in pr.labels or not label_events:
            reasons.append("no_grant_label")
        elif grantors:
            label_failures = [
                (event, _label_reasons(event, pr, agent_logins, grantors))
                for event in label_events
            ]
            if all(failures for _, failures in label_failures):
                _, latest_reasons = max(
                    label_failures, key=lambda item: item[0].created_at
                )
                reasons.extend(latest_reasons)

        records = [
            record
            for record in request.grants
            if record.repo == request.repo
            and record.pr_number == pr.number
            and record.head_sha == pr.head_sha
        ]
        passing_record = None
        if not records:
            reasons.append("no_grant_record")
        else:
            record_failures = []
            for record in records:
                failures = _record_reasons(record, request, agent_logins, grantors)
                if not failures:
                    passing_record = record
                    break
                record_failures.append(failures)
            if passing_record is None:
                reasons.extend(record_failures[0])

        if reasons:
            return ModelReleaseGateResult(
                verdict="REFUSED",
                allowed=False,
                exit_code=1,
                release_shape=release_shape,
                reasons=tuple(dict.fromkeys(reasons)),
            )
        assert passing_record is not None
        return ModelReleaseGateResult(
            verdict="GRANTED",
            allowed=True,
            exit_code=0,
            release_shape=release_shape,
            grant_id=passing_record.grant_id,
        )
