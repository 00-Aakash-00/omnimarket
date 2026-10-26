# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Collect GitHub facts for the pure release gate, refusing unreadable facts.

The injected fetch function returns parsed API responses with list pages joined.
The default uses gh's authentication and reads grants only from the policy source
ref. Everything that touches a file, the environment or the network lives here,
in an effect node, so the gate's handler stays pure.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from omnimarket.models.model_release_gate import (
    ModelReleaseGateIdentities,
    ModelReleaseGatePolicy,
    ModelReleaseGateRequest,
    ModelReleaseGrantRecord,
    ModelReleaseLabelEvent,
    ModelReleasePullRequestFacts,
    ReleaseEvent,
)
from omnimarket.nodes.node_release_gate_collect_effect.models.model_release_gate_collect import (
    ModelReleaseGateCollectRequest,
)


class NotFoundError(Exception):
    """The requested GitHub resource returned HTTP 404."""


def load_policy(path: str) -> ModelReleaseGatePolicy:
    return ModelReleaseGatePolicy.model_validate(yaml.safe_load(Path(path).read_text()))


def load_identities(path: str | None) -> ModelReleaseGateIdentities:
    if not path:
        return ModelReleaseGateIdentities()
    try:
        content = Path(path).read_text()
    except FileNotFoundError:
        return ModelReleaseGateIdentities()
    return ModelReleaseGateIdentities.model_validate(yaml.safe_load(content))


def _collect_pull_request(
    repo: str,
    number: int,
    fetch: Callable[[str], Any],
    policy: ModelReleaseGatePolicy,
) -> ModelReleasePullRequestFacts:
    pull_path = f"repos/{repo}/pulls/{number}"
    pr = fetch(pull_path)
    files = fetch(f"{pull_path}/files")
    commits = fetch(f"{pull_path}/commits")
    timeline = fetch(f"repos/{repo}/issues/{number}/timeline")
    head_ref = pr["head"]["ref"]
    head_sha = pr["head"]["sha"]
    activity = fetch(f"repos/{repo}/activity?ref=refs/heads/{head_ref}&per_page=100")
    head_push = next((entry for entry in activity if entry["after"] == head_sha), None)

    # Both linked identities matter; an unlinked git identity cannot be the App.
    commit_logins = {
        (commit.get(role) or {}).get("login") or f"unlinked:{index}"
        for index, commit in enumerate(commits)
        for role in ("committer", "author")
    }
    version_changed = any(
        file["filename"] in policy.version_files
        and re.search(r'^-\s*version\s*=\s*"', file.get("patch") or "", re.MULTILINE)
        and re.search(r'^\+\s*version\s*=\s*"', file.get("patch") or "", re.MULTILINE)
        for file in files
    )
    label_events = tuple(
        ModelReleaseLabelEvent.model_validate(
            {
                "label": entry["label"]["name"],
                "actor_login": entry["actor"]["login"],
                "actor_type": entry["actor"]["type"],
                "performed_via_app": bool(entry.get("performed_via_github_app")),
                "created_at": entry["created_at"],
            }
        )
        for entry in timeline
        if entry.get("event") == "labeled"
    )
    return ModelReleasePullRequestFacts.model_validate(
        {
            "number": pr["number"],
            "title": pr["title"],
            "head_ref": head_ref,
            "head_sha": head_sha,
            "base_ref": pr["base"]["ref"],
            "author_login": pr["user"]["login"],
            "author_type": pr["user"]["type"],
            "labels": tuple(label["name"] for label in pr["labels"]),
            "version_changed": version_changed,
            "head_push_actor_login": head_push["actor"]["login"] if head_push else "",
            "commit_author_logins": tuple(sorted(commit_logins)),
            "head_updated_at": head_push["timestamp"] if head_push else None,
            "label_events": label_events,
        }
    )


def _collect_grants(
    fetch: Callable[[str], Any], policy: ModelReleaseGatePolicy
) -> tuple[ModelReleaseGrantRecord, ...]:
    try:
        response = fetch(
            "repos/OmniNode-ai/onex_change_control/contents/grants/release_grants.yaml"
            f"?ref={policy.grant_source_ref}"
        )
    except NotFoundError:
        return ()
    except subprocess.CalledProcessError as exc:
        # Unreadable grants must not block pull requests that are not releases:
        # no grants means a release-shaped PR is refused, which is the closed side.
        sys.stderr.write(f"release-gate: grants unreadable, none assumed: {exc}\n")
        return ()
    document = yaml.safe_load(base64.b64decode(response["content"]))
    return tuple(
        ModelReleaseGrantRecord.model_validate(
            {**record, "source_ref": policy.grant_source_ref}
        )
        for record in document["grants"]
    )


def collect_request(
    *,
    repo: str,
    event_name: str,
    payload: dict[str, Any],
    fetch: Callable[[str], Any],
    policy: ModelReleaseGatePolicy,
    identities: ModelReleaseGateIdentities,
    now: datetime,
) -> ModelReleaseGateRequest:
    event: ReleaseEvent
    pr = None
    tag = ""
    grants: tuple[ModelReleaseGrantRecord, ...] = ()
    if event_name in ("pull_request", "pull_request_target"):
        event = "pull_request"
        pr = _collect_pull_request(
            repo, payload["pull_request"]["number"], fetch, policy
        )
        grants = _collect_grants(fetch, policy)
    elif event_name == "push":
        ref = payload["ref"]
        if not ref.startswith("refs/tags/"):
            raise ValueError(f"not a release event: {ref}")
        event = "tag_push"
        tag = ref.removeprefix("refs/tags/")
    elif event_name == "workflow_dispatch":
        event = "dispatch"
    elif event_name == "release":
        event = "release_published"
    else:
        raise ValueError(f"not a release event: {event_name}")
    return ModelReleaseGateRequest(
        repo=repo,
        event=event,
        actor_login=payload["sender"]["login"],
        actor_type=payload["sender"]["type"],
        now=now,
        policy=policy,
        identities=identities,
        pull_request=pr,
        tag=tag,
        grants=grants,
    )


def gh_fetch(path: str) -> Any:
    command = ["gh", "api", path, "--paginate"]
    if path.split("?", 1)[0].endswith("/timeline"):
        command.extend(["-H", "Accept: application/vnd.github+json"])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        if "404" in completed.stderr:
            raise NotFoundError(path)
        raise subprocess.CalledProcessError(completed.returncode, command)

    # gh emits one JSON document per page; concatenate array pages only.
    decoder = json.JSONDecoder()
    remaining = completed.stdout.strip()
    pages = []
    while remaining:
        page, end = decoder.raw_decode(remaining)
        pages.append(page)
        remaining = remaining[end:].lstrip()
    if not pages:
        raise ValueError("empty GitHub API response")
    if len(pages) == 1:
        return pages[0]
    if not all(isinstance(page, list) for page in pages):
        raise ValueError("expected arrays for paginated GitHub API response")
    return [entry for page in pages for entry in page]


class HandlerReleaseGateCollect:
    """Effect handler: GitHub event and API facts in, one gate request out."""

    def __init__(self, fetch: Callable[[str], Any] | None = None) -> None:
        self._fetch = fetch

    @property
    def handler_type(self) -> Literal["NODE_HANDLER"]:
        return "NODE_HANDLER"

    @property
    def handler_category(self) -> Literal["EFFECT"]:
        return "EFFECT"

    def handle(
        self, request: ModelReleaseGateCollectRequest
    ) -> ModelReleaseGateRequest:
        return collect_request(
            repo=request.repo,
            event_name=request.event_name,
            payload=json.loads(Path(request.event_path).read_text()),
            fetch=self._fetch or gh_fetch,
            policy=load_policy(request.policy_path),
            identities=load_identities(request.identities_path or None),
            now=datetime.now(UTC),
        )
