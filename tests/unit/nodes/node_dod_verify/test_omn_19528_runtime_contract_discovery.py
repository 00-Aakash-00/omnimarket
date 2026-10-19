# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-19528 -- dod_verify finds a ticket's product contract on a clone-less runtime.

The deployed runtime has an ``$OMNI_HOME`` directory but no canonical clones in
it. ``contract_repositories`` asked the clones which repository carried
``contracts/<ticket>.yaml``, got an empty answer, and every delegation-linked
verdict ended ``CONTRACT_MISSING`` -- so the routing overlay never saw one
decided verdict. With no clone to ask, discovery now names the declared product
repositories and the merged-PR listing reads each through GitHub, once per
ticket per window.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnimarket.nodes.node_dod_verify.handlers import (
    handler_dod_evidence_github_effect as hd_mod,
)
from omnimarket.nodes.node_dod_verify.handlers.dod_evidence_local_source import (
    RUNTIME_CONTRACT_REPOSITORIES,
    DodEvidenceLocalSource,
)
from omnimarket.nodes.node_dod_verify.handlers.handler_dod_evidence_github_effect import (
    HandlerDodEvidenceGithubEffect,
)
from omnimarket.nodes.node_dod_verify.models.model_dod_evidence_github_lookup import (
    EnumDodEvidenceGithubOperation,
    ModelDodEvidenceGithubLookupCommand,
    ModelDodEvidenceGithubLookupResultEvent,
)
from tests.unit.nodes.node_dod_verify.omn_20886_repo_world import World
from tests.unit.nodes.node_dod_verify.omn_21146_world_gateway import WorldGateway

_TICKET = "OMN-19528"
_MARKET = "OmniNode-ai/omnimarket"


def _install(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    state_path = world.root / "state.json"
    state_path.write_text(json.dumps(world.state))
    monkeypatch.setenv("OMNI_HOME", str(world.registry))
    monkeypatch.setenv("ONEX_PR_WATCHER_STATE", str(state_path))
    monkeypatch.setattr(hd_mod, "_default_local_source", DodEvidenceLocalSource)


def _list_command() -> ModelDodEvidenceGithubLookupCommand:
    return ModelDodEvidenceGithubLookupCommand(
        operation=EnumDodEvidenceGithubOperation.LIST_CONTRACT_REPO_MERGED_PRS,
        ticket_id=_TICKET,
    )


def _listed() -> ModelDodEvidenceGithubLookupResultEvent:
    event = HandlerDodEvidenceGithubEffect().handle(_list_command()).events[0]
    assert isinstance(event, ModelDodEvidenceGithubLookupResultEvent)
    return event


def _pr_record(number: int) -> dict[str, object]:
    return {
        "repo": "omnimarket",
        "number": number,
        "head_ref": f"omn-19528-{number}",
        "head_sha": "a" * 40,
        "merge_sha": "b" * 40,
        "merged_at": "2026-10-19T00:00:00Z",
        "base": "dev",
    }


@pytest.mark.unit
def test_clone_less_registry_root_names_the_declared_product_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    _install(world, monkeypatch)

    repos = DodEvidenceLocalSource().contract_repositories(_TICKET)

    assert repos == sorted(RUNTIME_CONTRACT_REPOSITORIES)
    assert _MARKET in repos
    assert "OmniNode-ai/onex_change_control" not in repos


@pytest.mark.unit
def test_registry_root_with_a_clone_still_asks_the_clones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Positive control: a host that has clones keeps the exact answer, which
    # here is that no clone's default branch ever carried the contract.
    world = World(tmp_path)
    world.clone("omnimarket")
    _install(world, monkeypatch)

    assert DodEvidenceLocalSource().contract_repositories(_TICKET) == []


@pytest.mark.unit
def test_unset_omni_home_is_still_unanswerable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OMNI_HOME", raising=False)

    assert DodEvidenceLocalSource().contract_repositories(_TICKET) is None


@pytest.mark.unit
def test_merged_prs_of_a_clone_less_runtime_are_listed_through_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    world.github_prs["omnimarket#4200"] = _pr_record(4200)
    _install(world, monkeypatch)
    searched: list[str] = []

    def gh(argv: list[str], timeout_s: int) -> tuple[object | None, str]:
        repo = argv[argv.index("--repo") + 1]
        searched.append(repo)
        assert argv[argv.index("--search") + 1] == _TICKET
        if repo == _MARKET:
            return [
                {
                    "number": 4200,
                    "title": f"feat({_TICKET}): the work",
                    "headRefName": "omn-19528-work",
                }
            ], ""
        return [], ""

    monkeypatch.setattr(hd_mod, "_gh_json", gh)
    gateway = WorldGateway(world.check_runs, world.github_prs)
    monkeypatch.setattr(hd_mod, "_default_gateway_client", lambda: gateway)

    event = _listed()

    assert event.resolved is True
    assert [(pr.repo, pr.pr_number) for pr in event.merged_prs] == [(_MARKET, 4200)]
    assert sorted(searched) == sorted(RUNTIME_CONTRACT_REPOSITORIES)


@pytest.mark.unit
def test_the_listing_is_read_once_per_ticket_inside_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    _install(world, monkeypatch)
    calls: list[list[str]] = []

    def gh(argv: list[str], timeout_s: int) -> tuple[object | None, str]:
        calls.append(argv)
        return [], ""

    monkeypatch.setattr(hd_mod, "_gh_json", gh)

    _listed()
    first = len(calls)
    _listed()

    assert first == len(RUNTIME_CONTRACT_REPOSITORIES)
    assert len(calls) == first


@pytest.mark.unit
def test_the_listing_is_read_again_after_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    _install(world, monkeypatch)
    calls: list[list[str]] = []
    clock = [1000.0]

    def gh(argv: list[str], timeout_s: int) -> tuple[object | None, str]:
        calls.append(argv)
        return [], ""

    monkeypatch.setattr(hd_mod, "_gh_json", gh)
    monkeypatch.setattr(hd_mod, "_monotonic", lambda: clock[0])

    _listed()
    first = len(calls)
    clock[0] += hd_mod._MERGED_LISTING_TTL_S + 1
    _listed()

    assert len(calls) == 2 * first


@pytest.mark.unit
def test_an_unreadable_listing_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    _install(world, monkeypatch)
    calls: list[list[str]] = []

    def gh(argv: list[str], timeout_s: int) -> tuple[object | None, str]:
        calls.append(argv)
        return None, "HTTP 502"

    monkeypatch.setattr(hd_mod, "_gh_json", gh)

    assert _listed().resolved is False
    assert _listed().resolved is False
    assert len(calls) == 2
