# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Apply the release gate rulesets declared under .github/rulesets (OMN-21433).

The declarations are the source of truth; this handler makes the repository's
rulesets match them and is run by apply-release-gate-rulesets.yml. Nobody
changes these rulesets by hand.

Default is a plan: it lists the rulesets, reports create/update per declaration
and writes nothing. ``apply`` writes. The release train's App id is substituted
for ``${RELEASE_TRAIN_APP_ID}``, so no number is committed that the workflows do
not already name, and an unset id refuses rather than leaving a ruleset with no
bypass actor.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from omnimarket.nodes.node_release_gate_rulesets_effect.models.model_release_gate_rulesets import (
    ModelReleaseGateRulesetsRequest,
    ModelReleaseGateRulesetsResult,
)

PLACEHOLDER = "${RELEASE_TRAIN_APP_ID}"
Api = Callable[[str, str, dict[str, Any] | None], Any]


def load_declarations(directory: Path, app_id: str) -> list[dict[str, Any]]:
    declarations = []
    for path in sorted(directory.glob("*.json")):
        text = path.read_text()
        if PLACEHOLDER in text:
            if not app_id.isdigit():
                raise ValueError(
                    f"{path.name} needs RELEASE_TRAIN_APP_ID set to the App's numeric id"
                )
            text = text.replace(f'"{PLACEHOLDER}"', app_id)
        declarations.append(json.loads(text))
    if not declarations:
        raise ValueError(f"no ruleset declarations under {directory}")
    return declarations


def plan(
    declarations: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> list[tuple[str, dict[str, Any], int | None]]:
    by_name = {ruleset["name"]: ruleset for ruleset in existing}
    steps: list[tuple[str, dict[str, Any], int | None]] = []
    for declared in declarations:
        current = by_name.get(declared["name"])
        if current is None:
            steps.append(("create", declared, None))
        else:
            steps.append(("update", declared, int(current["id"])))
    return steps


def gh_api(method: str, path: str, body: dict[str, Any] | None) -> Any:
    command = ["gh", "api", "--method", method, path]
    if body is not None:
        command.extend(["--input", "-"])
    completed = subprocess.run(
        command,
        input=None if body is None else json.dumps(body),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout) if completed.stdout.strip() else None


class HandlerReleaseGateRulesets:
    """Effect handler; ``api`` is injectable so tests never reach GitHub."""

    def __init__(self, api: Api = gh_api) -> None:
        self._api = api

    @property
    def handler_type(self) -> Literal["NODE_HANDLER"]:
        return "NODE_HANDLER"

    @property
    def handler_category(self) -> Literal["EFFECT"]:
        return "EFFECT"

    def handle(
        self, request: ModelReleaseGateRulesetsRequest
    ) -> ModelReleaseGateRulesetsResult:
        declarations = load_declarations(Path(request.directory), request.train_app_id)
        existing = self._api("GET", f"repos/{request.repo}/rulesets", None) or []
        lines = []
        for action, declared, ruleset_id in plan(declarations, existing):
            mode = "APPLY" if request.apply else "PLAN"
            lines.append(f"{mode} {action} {declared['name']}")
            if not request.apply:
                continue
            if action == "create":
                self._api("POST", f"repos/{request.repo}/rulesets", declared)
            else:
                self._api(
                    "PUT", f"repos/{request.repo}/rulesets/{ruleset_id}", declared
                )
        return ModelReleaseGateRulesetsResult(lines=tuple(lines))
