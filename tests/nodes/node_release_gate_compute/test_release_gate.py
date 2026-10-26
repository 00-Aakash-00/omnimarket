# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-21433: only the release train releases; anything else needs a human's grant.

Each refusal test has a falsifier: the same request with one fact corrected
passes, so the refusal is caused by that fact and not by the fixture.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from release_gate_builders import (
    HEAD,
    IDENTITIES,
    NOW,
    TRAIN,
    agent_release_pr,
    grant,
    granted_pr,
    label_event,
    load_policy,
    pr,
    request,
)

from omnimarket.handlers.handler_release_gate import HandlerReleaseGate
from omnimarket.models.model_release_gate import ModelReleaseGateIdentities

pytestmark = pytest.mark.unit

POLICY = load_policy()


def decide(**kw):
    return HandlerReleaseGate().handle(request(POLICY, **kw))


# 1. a release by the train passes --------------------------------------------


def test_train_release_pr_passes() -> None:
    result = decide(pull_request=pr())
    assert (result.verdict, result.allowed, result.exit_code) == ("TRAIN", True, 0)
    assert result.release_shape  # it was judged release-shaped, then trusted


def test_train_post_release_dev_bump_passes() -> None:
    result = decide(
        pull_request=pr(
            title="chore(release): open dev for v0.5.1",
            head_ref="automation/post-release-dev-bump-0.5.1",
        )
    )
    assert result.verdict == "TRAIN"


def test_train_tag_push_passes() -> None:
    assert decide(event="tag_push", tag="v0.5.0").verdict == "TRAIN"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("head_push_actor_login", "lab-bot"),  # an agent pushed onto the train branch
        ("commit_author_logins", (TRAIN, "lab-bot")),  # an agent added a commit
        ("author_login", "lab-bot"),  # opened by someone else
        ("author_type", "User"),  # a person-type account cannot be the App
        ("head_ref", "OMN-1-release"),  # not the train's branch shape
        ("head_push_actor_login", ""),  # push actor unreadable: fail closed
    ],
)
def test_train_shape_is_not_enough_when_one_train_fact_is_wrong(
    field: str, value: object
) -> None:
    """Falsifier for test_train_release_pr_passes: one wrong fact removes TRAIN."""
    result = decide(pull_request=pr(**{field: value}))
    assert result.verdict == "REFUSED"
    assert result.exit_code == 1


# 2. a release PR without a grant is refused ----------------------------------


def test_release_pr_without_grant_is_refused() -> None:
    result = decide(pull_request=agent_release_pr(), actor="lab-bot", actor_type="User")
    assert (result.verdict, result.allowed, result.exit_code) == ("REFUSED", False, 1)
    assert "no_grant_label" in result.reasons
    assert "no_grant_record" in result.reasons


def test_release_pr_with_label_but_no_record_is_refused() -> None:
    result = decide(pull_request=granted_pr(), actor="lab-bot", actor_type="User")
    assert result.verdict == "REFUSED"
    assert result.reasons == ("no_grant_record",)


def test_release_pr_with_record_but_no_label_is_refused() -> None:
    result = decide(pull_request=agent_release_pr(), grants=(grant(),), actor="lab-bot")
    assert result.verdict == "REFUSED"
    assert result.reasons == ("no_grant_label",)


@pytest.mark.parametrize(
    "over",
    [
        {
            "title": "docs: tidy",
            "version_changed": True,
        },  # version bump, innocent title
        {"title": "release: omnimarket 0.5.2", "version_changed": False},
        {"title": "chore(release): 0.5.2", "version_changed": False},
        {"title": "tidy", "labels": ("release",), "version_changed": False},
        {"title": "tidy", "head_ref": "release/0.5.2", "version_changed": False},
    ],
)
def test_every_release_shape_is_refused_without_grant(over: dict[str, object]) -> None:
    result = decide(pull_request=agent_release_pr(**over), actor="lab-bot")
    assert result.verdict == "REFUSED"


def test_ordinary_pr_is_not_release_shaped_and_passes() -> None:
    """Falsifier for the shape tests: the same author, no release shape, passes."""
    result = decide(
        pull_request=agent_release_pr(
            title="fix: handler", head_ref="OMN-1-fix", version_changed=False
        ),
        actor="lab-bot",
    )
    assert (result.verdict, result.allowed) == ("NOT_RELEASE", True)


def test_dev_merge_tag_is_not_a_release_tag() -> None:
    assert decide(
        event="tag_push", tag="dev-20261027120000-abcdef012345", actor="x"
    ).allowed


# 3. a grant by a non-allowlisted login or an agent identity is refused -------


@pytest.mark.parametrize(
    ("labeler", "labeler_type", "via_app", "reason"),
    [
        ("stranger", "User", False, "label_actor_not_grantor"),
        ("lab-bot", "User", False, "label_actor_not_grantor"),  # agent, not a grantor
        ("grantor-op", "Bot", False, "label_actor_not_a_person"),
        ("grantor-op", "User", True, "label_actor_not_a_person"),  # via an App token
    ],
)
def test_label_from_wrong_actor_is_refused(
    labeler: str, labeler_type: str, via_app: bool, reason: str
) -> None:
    facts = granted_pr(
        label_events=(label_event(labeler, actor_type=labeler_type, via_app=via_app),)
    )
    result = decide(pull_request=facts, grants=(grant(),), actor="lab-bot")
    assert result.verdict == "REFUSED"
    assert reason in result.reasons


def test_grantor_login_that_is_also_an_agent_login_never_grants() -> None:
    """A lab host signed in to a person's account: listing it as an agent login
    removes that login's power to grant, even though it is a grantor."""
    identities = ModelReleaseGateIdentities(
        grantors=IDENTITIES.grantors,
        agent_logins=(*IDENTITIES.agent_logins, "grantor-op"),
    )
    result = decide(
        pull_request=granted_pr(), grants=(grant(),), actor="x", identities=identities
    )
    assert result.verdict == "REFUSED"
    assert "label_actor_is_agent" in result.reasons


def test_labeler_who_authored_the_pr_cannot_grant_it() -> None:
    facts = granted_pr(author_login="grantor-op")
    result = decide(pull_request=facts, grants=(grant(requested_by="x"),), actor="x")
    assert result.verdict == "REFUSED"
    assert "label_actor_is_requester" in result.reasons


def test_label_applied_before_the_last_push_is_stale() -> None:
    stale = label_event(at=NOW - timedelta(hours=2))
    facts = granted_pr(label_events=(stale,), head_updated_at=NOW - timedelta(hours=1))
    result = decide(pull_request=facts, grants=(grant(),), actor="lab-bot")
    assert result.verdict == "REFUSED"
    assert "label_older_than_head" in result.reasons


def test_removed_label_does_not_count() -> None:
    facts = granted_pr(labels=())  # event history says labeled; current labels say not
    result = decide(pull_request=facts, grants=(grant(),), actor="lab-bot")
    assert result.verdict == "REFUSED"
    assert "no_grant_label" in result.reasons


def test_no_overlay_means_nobody_can_grant() -> None:
    result = decide(
        pull_request=granted_pr(),
        grants=(grant(),),
        actor="lab-bot",
        identities=ModelReleaseGateIdentities(),
    )
    assert result.verdict == "REFUSED"
    assert "grantors_unavailable" in result.reasons


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"approved_by": "stranger"}, "approver_not_grantor"),
        ({"approved_by": "lab-bot"}, "approver_is_agent"),
        ({"approved_by": "grantor-op", "requested_by": "grantor-op"}, "self_granted"),
        ({"expires_at": NOW - timedelta(seconds=1)}, "grant_expired"),
        ({"head_sha": "b" * 40}, "no_grant_record"),  # bound to another head
        ({"pr_number": 78}, "no_grant_record"),
        ({"repo": "OmniNode-ai/other"}, "no_grant_record"),
        ({"source_ref": "OMN-1-branch"}, "grant_not_from_source_ref"),
        ({"consumed": True}, "grant_consumed"),
    ],
)
def test_bad_grant_record_is_refused(over: dict[str, object], reason: str) -> None:
    result = decide(pull_request=granted_pr(), grants=(grant(**over),), actor="lab-bot")
    assert result.verdict == "REFUSED"
    assert reason in result.reasons


# 4. a valid grant passes ------------------------------------------------------


@pytest.mark.parametrize("grantor", ["grantor-op", "grantor-co"])
def test_valid_grant_passes(grantor: str) -> None:
    result = decide(
        pull_request=granted_pr(label_events=(label_event(grantor),)),
        grants=(grant(approved_by=grantor),),
        actor="lab-bot",
    )
    assert (result.verdict, result.allowed, result.exit_code) == ("GRANTED", True, 0)
    assert result.grant_id == "rg-0001"


def test_grant_follows_the_head_it_was_made_for() -> None:
    """Falsifier for test_valid_grant_passes: a new push moves the head; the old
    grant no longer matches and the PR is refused until it is granted again."""
    result = decide(
        pull_request=granted_pr(head_sha="c" * 40),
        grants=(grant(),),
        actor="lab-bot",
    )
    assert result.verdict == "REFUSED"
    assert HEAD != "c" * 40


# tags and dispatches ------------------------------------------------------------


@pytest.mark.parametrize("tag", ["v0.5.0", "v1.2.3-rc1"])
def test_release_tag_by_anyone_but_the_train_is_refused(tag: str) -> None:
    result = decide(event="tag_push", tag=tag, actor="grantor-op", actor_type="User")
    assert result.verdict == "REFUSED"
    assert "tag_actor_not_train" in result.reasons


def test_a_grant_cannot_authorise_a_hand_pushed_tag() -> None:
    result = decide(event="tag_push", tag="v0.5.0", actor="lab-bot", grants=(grant(),))
    assert result.verdict == "REFUSED"


@pytest.mark.parametrize("event", ["dispatch", "release_published"])
def test_dispatch_and_published_release_need_the_train(event: str) -> None:
    refused = decide(event=event, actor="lab-bot", actor_type="User")
    assert refused.verdict == "REFUSED"
    assert "release_actor_not_train" in refused.reasons
    assert decide(event=event).verdict == "TRAIN"  # falsifier: same event, train actor


def test_train_name_without_bot_type_is_not_the_train() -> None:
    result = decide(event="dispatch", actor=TRAIN, actor_type="User")
    assert result.verdict == "REFUSED"


def test_pull_request_event_without_facts_fails_closed() -> None:
    result = decide(pull_request=None, actor="lab-bot")
    assert result.verdict == "REFUSED"
    assert result.reasons == ("pull_request_facts_missing",)
