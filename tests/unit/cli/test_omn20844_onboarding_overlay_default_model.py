# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Onboarding takes its default OpenRouter model from the overlay (OMN-20844, AC3).

Operator words, 2026-10-15 in session: "yes, with your recommendation. no
fucking hardcoded anything". The recommendation: onboarding uses the default
model the onboarding overlay declares, never one written in code, and the
customer can change it afterwards with one command.

* with the overlay field set, ``onex secret set`` and ``onex models add`` store
  that model for an OpenRouter key when no ``--model`` is given, after checking
  it against the key's own model list;
* an explicit ``--model`` beats the overlay;
* with no overlay, nothing is picked: a script is refused, as before;
* ``onex models use`` changes the model with one command and no key;
* falsifier: the files that run onboarding and the BYOK path name no model id.

Fakes only: the provider's model list is a stub and no network is used. The
model ids below are fixtures this file declares.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from omnimarket.cli.cli_secret import models_group, secret_group
from omnimarket.handlers.node_overlay_reader import OVERLAY_ROOTS_ENV
from omnimarket.inference.local_byok_credential_adapter import (
    LocalByokCredentialStore,
    resolve_local_byok_credential_model,
    resolve_local_byok_credential_models,
)
from omnimarket.routing import byok_model_discovery

pytestmark = pytest.mark.unit

_REF = "llm.openrouter.api_key"
_KEY = "sk-or-v1-synthetic-omn20844-ac3-key-never-printed"
_DEFAULT = "fixture-lab/default-model"
_OTHER = "fixture-lab/other-model"
_UNLISTED = "fixture-lab/not-in-the-list"
_LISTED = [_DEFAULT, _OTHER]
_OVERLAY_NODE = "node_model_setup_effect"
_SRC = Path(__file__).resolve().parents[3] / "src" / "omnimarket"


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "delegation.sqlite"
    monkeypatch.setattr(
        "omnimarket.inference.local_byok_credential_adapter.default_evidence_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(
        "omnimarket.nodes.node_model_setup_effect.handlers.handler_model_setup.run_pinned_delegation",
        lambda _backend_id: (1, "", "onex delegate failed: stubbed test delegation"),
    )

    def listed(*, url: str, **_: Any) -> Any:
        return {"data": [{"id": model} for model in _LISTED]}

    monkeypatch.setattr(byok_model_discovery, "get_models_json", listed)
    monkeypatch.delenv(OVERLAY_ROOTS_ENV, raising=False)
    return db_path


def _overlay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: object) -> Path:
    root = tmp_path / "overlays"
    path = root / _OVERLAY_NODE / "overlay.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    monkeypatch.setenv(OVERLAY_ROOTS_ENV, str(root))
    return path


def _declare_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _overlay(monkeypatch, tmp_path, {"openrouter_default_model": _DEFAULT})


def _stored_key() -> str | None:
    import asyncio

    return asyncio.run(LocalByokCredentialStore().get_secret(_REF))


class TestOverlayDefault:
    def test_secret_set_with_no_model_stores_the_overlay_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _declare_default(monkeypatch, tmp_path)
        result = CliRunner().invoke(secret_group, ["set", _REF], input=f"{_KEY}\n")

        assert result.exit_code == 0, result.output
        assert resolve_local_byok_credential_model("openrouter") == _DEFAULT
        assert "onex models use openrouter" in result.output
        assert _KEY not in result.output

    def test_models_add_with_no_model_stores_the_overlay_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _declare_default(monkeypatch, tmp_path)
        CliRunner().invoke(models_group, ["add", "openrouter"], input=f"{_KEY}\n")

        assert resolve_local_byok_credential_models("openrouter") == (_DEFAULT,)

    def test_an_explicit_model_beats_the_overlay_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _declare_default(monkeypatch, tmp_path)
        result = CliRunner().invoke(
            secret_group, ["set", _REF, "--model", _OTHER], input=f"{_KEY}\n"
        )

        assert result.exit_code == 0, result.output
        assert resolve_local_byok_credential_model("openrouter") == _OTHER

    def test_a_default_the_keys_list_does_not_name_is_refused_and_nothing_stored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _overlay(monkeypatch, tmp_path, {"openrouter_default_model": _UNLISTED})
        result = CliRunner().invoke(secret_group, ["set", _REF], input=f"{_KEY}\n")

        assert result.exit_code != 0
        assert "BYOK_MODEL_NOT_LISTED" in result.output
        assert _stored_key() is None

    def test_no_overlay_picks_nothing_and_a_script_is_refused(self) -> None:
        # Positive control for the tests above: the same call without the
        # overlay is the pre-existing refusal, so the default came from the overlay.
        result = CliRunner().invoke(secret_group, ["set", _REF], input=f"{_KEY}\n")

        assert result.exit_code != 0
        assert "BYOK_MODEL_NOT_CHOSEN" in result.output
        assert _stored_key() is None

    def test_an_overlay_without_the_field_picks_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _overlay(monkeypatch, tmp_path, {"gemini_default_model": _DEFAULT})
        result = CliRunner().invoke(secret_group, ["set", _REF], input=f"{_KEY}\n")

        assert result.exit_code != 0
        assert "BYOK_MODEL_NOT_CHOSEN" in result.output

    @pytest.mark.parametrize(
        "content",
        [
            {"openrouter_default_model": ""},
            {"openrouter_default_model": 7},
            {"openrouter_model": _DEFAULT},
            ["openrouter_default_model"],
        ],
    )
    def test_a_broken_overlay_is_refused_not_read_as_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: object
    ) -> None:
        _overlay(monkeypatch, tmp_path, content)
        result = CliRunner().invoke(secret_group, ["set", _REF], input=f"{_KEY}\n")

        assert result.exit_code != 0
        assert OVERLAY_ROOTS_ENV in result.output
        assert _stored_key() is None


class TestOneCommandOverride:
    @staticmethod
    def _onboard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _declare_default(monkeypatch, tmp_path)
        done = CliRunner().invoke(secret_group, ["set", _REF], input=f"{_KEY}\n")
        assert done.exit_code == 0, done.output

    def test_use_changes_the_model_with_one_command_and_no_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._onboard(monkeypatch, tmp_path)
        done = CliRunner().invoke(models_group, ["use", "openrouter", _OTHER])

        assert done.exit_code == 0, done.output
        assert resolve_local_byok_credential_models("openrouter") == (_OTHER,)
        assert resolve_local_byok_credential_model("openrouter") == _OTHER
        assert _stored_key() == _KEY

    def test_use_refuses_a_model_the_keys_list_does_not_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._onboard(monkeypatch, tmp_path)
        done = CliRunner().invoke(models_group, ["use", "openrouter", _UNLISTED])

        assert done.exit_code != 0
        assert "BYOK_MODEL_NOT_LISTED" in done.output
        assert resolve_local_byok_credential_models("openrouter") == (_DEFAULT,)

    def test_use_with_no_key_stored_names_how_to_add_one(self) -> None:
        done = CliRunner().invoke(models_group, ["use", "openrouter", _OTHER])

        assert done.exit_code != 0
        assert "onex models add openrouter" in done.output

    def test_use_does_not_apply_to_ollama(self) -> None:
        done = CliRunner().invoke(models_group, ["use", "ollama", _OTHER])

        assert done.exit_code != 0
        assert "Ollama" in done.output


# What a model id looks like when someone writes one into code or a help text.
_MODEL_ID = re.compile(
    r"\b(?:claude-(?:opus|sonnet|haiku|fable|instant|\d)|gpt-?\d|o[1-9]-(?:mini|preview)"
    r"|gemini-?\d|qwen-?\d|qwen-|glm-?\d|deepseek-|llama-?\d|mistral-|mixtral-"
    r"|codestral|gemma-?\d|grok-?\d|kimi-|nemotron-?\d?)",
    re.IGNORECASE,
)
# Onboarding and the BYOK path. The catalogue config file is not here: its
# `model_preference` patterns are a separate migration (see the PR body).
_ONBOARDING_BYOK_FILES = (
    "cli/cli_secret.py",
    "nodes/node_model_setup_effect/handlers/handler_model_setup.py",
    "nodes/node_model_setup_effect/contract.yaml",
    "routing/byok_model_discovery.py",
    "routing/byok_provider_backends.py",
)


def _model_id_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _MODEL_ID.search(line)]


class TestNoModelIdInOnboardingCode:
    def test_the_detector_finds_a_planted_id(self) -> None:
        # Positive control: the scan below is not vacuous.
        assert _model_id_lines('DEFAULT = "gemini-2.5-flash-lite"\n# nothing here\n')
        assert not _model_id_lines("DEFAULT = None  # the overlay names it\n")

    @pytest.mark.parametrize("relative", _ONBOARDING_BYOK_FILES)
    def test_the_file_names_no_model_id(self, relative: str) -> None:
        text = (_SRC / relative).read_text(encoding="utf-8")
        assert _model_id_lines(text) == []
