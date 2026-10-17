# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Onboarding takes the OpenRouter model from the overlay (OMN-20844, AC3).

AC3 as amended: onboarding selects the default OpenRouter model declared in
the onboarding overlay, with no model id in code, and the customer can change
it with one documented command (``onex models use``). Nobody is asked, and
nothing is picked from a list in code.

Fakes only: the provider's model list is a stub, no network. Every model id
below is a fixture this file declares.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from omnimarket.cli import cli_secret
from omnimarket.cli.cli_secret import models_group, secret_group
from omnimarket.inference.local_byok_credential_adapter import (
    LocalByokCredentialStore,
    resolve_local_byok_credential_model,
    resolve_local_byok_credential_models,
)
from omnimarket.routing import byok_model_discovery
from tests.node_overlay_support import install_node_overlay

pytestmark = pytest.mark.unit

_REF = "llm.openrouter.api_key"
_KEY = "sk-or-v1-synthetic-omn20844-ac3-key-never-printed"
_DEFAULT = "fixture-lab/default-model"
_OTHER = "fixture-lab/other-model"
_THIRD = "fixture-lab/third-model"
_LISTED = [_DEFAULT, _OTHER, _THIRD]
_ONBOARDING = "node_onboarding"
_FIELD = "default_openrouter_model"


class _Models:
    def __init__(self, listed: list[str]) -> None:
        self.listed = listed

    def __call__(self, *, url: str, **_: Any) -> Any:
        return {"data": [{"id": model} for model in self.listed]}


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
    monkeypatch.setattr(byok_model_discovery, "get_models_json", _Models(_LISTED))
    monkeypatch.delenv("ONEX_SKILL_OVERLAY_ROOTS", raising=False)
    return db_path


def _overlay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, model: str) -> None:
    install_node_overlay(
        monkeypatch, tmp_path / "overlay", _ONBOARDING, {_FIELD: model}
    )


def _stored_key() -> str | None:
    return asyncio.run(LocalByokCredentialStore().get_secret(_REF))


def _set(args: list[str] | None = None) -> Any:
    return CliRunner().invoke(
        secret_group, ["set", _REF, *(args or [])], input=f"{_KEY}\n"
    )


class TestOverlayDefault:
    def test_secret_set_with_no_model_stores_the_overlay_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _overlay(monkeypatch, tmp_path, _DEFAULT)
        result = _set()

        assert result.exit_code == 0, result.output
        assert resolve_local_byok_credential_model("openrouter") == _DEFAULT
        assert "onboarding overlay" in result.output
        assert "onex models use openrouter" in result.output
        assert _KEY not in result.output

    def test_an_explicit_model_beats_the_overlay_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _overlay(monkeypatch, tmp_path, _DEFAULT)
        result = _set(["--model", _OTHER])

        assert result.exit_code == 0, result.output
        assert resolve_local_byok_credential_model("openrouter") == _OTHER

    def test_an_overlay_default_the_key_list_does_not_name_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _overlay(monkeypatch, tmp_path, "fixture-lab/not-listed")
        result = _set()

        assert result.exit_code != 0
        assert "BYOK_MODEL_NOT_LISTED" in result.output
        assert _stored_key() is None

    def test_models_add_with_no_model_uses_the_overlay_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _overlay(monkeypatch, tmp_path, _DEFAULT)
        result = CliRunner().invoke(
            models_group, ["add", "openrouter"], input=f"{_KEY}\n"
        )

        assert resolve_local_byok_credential_model("openrouter") == _DEFAULT
        assert "BYOK_MODEL_NOT_CHOSEN" not in result.output


class TestNoOverlay:
    def test_no_overlay_and_no_model_is_refused_naming_the_overlay_field(self) -> None:
        result = _set()

        assert result.exit_code != 0
        assert "BYOK_MODEL_NOT_CHOSEN" in result.output
        assert _FIELD in result.output
        assert "--model" in result.output
        assert _stored_key() is None

    def test_a_terminal_is_not_asked_which_model(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _overlay(monkeypatch, tmp_path, _DEFAULT)
        monkeypatch.setattr(cli_secret, "_stdin_is_tty", lambda: True)
        monkeypatch.setattr(cli_secret, "getpass", lambda _prompt="": _KEY)

        def _never(*_: Any, **__: Any) -> str:
            raise AssertionError("onboarding asked the developer for a model")

        monkeypatch.setattr(cli_secret.click, "prompt", _never)
        result = CliRunner().invoke(secret_group, ["set", _REF])

        assert result.exit_code == 0, result.output
        assert resolve_local_byok_credential_model("openrouter") == _DEFAULT

    def test_a_broken_overlay_is_refused_not_read_as_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        install_node_overlay(
            monkeypatch, tmp_path / "overlay", _ONBOARDING, {"unknown_field": "x"}
        )
        result = _set()

        assert result.exit_code != 0
        assert "unknown_field" in result.output
        assert _stored_key() is None


class TestOneCommandOverride:
    def _store(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _overlay(monkeypatch, tmp_path, _DEFAULT)
        assert _set().exit_code == 0

    def test_use_puts_a_listed_model_first_and_keeps_the_rest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._store(monkeypatch, tmp_path)
        result = CliRunner().invoke(models_group, ["use", "openrouter", _OTHER])

        assert result.exit_code == 0, result.output
        assert resolve_local_byok_credential_models("openrouter") == (_OTHER, _DEFAULT)
        assert resolve_local_byok_credential_model("openrouter") == _OTHER

    def test_use_a_model_already_held_just_moves_it_first(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._store(monkeypatch, tmp_path)
        assert (
            CliRunner().invoke(models_group, ["use", "openrouter", _OTHER]).exit_code
            == 0
        )
        result = CliRunner().invoke(models_group, ["use", "openrouter", _DEFAULT])

        assert result.exit_code == 0, result.output
        assert resolve_local_byok_credential_models("openrouter") == (_DEFAULT, _OTHER)

    def test_use_a_model_the_key_list_does_not_name_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._store(monkeypatch, tmp_path)
        result = CliRunner().invoke(
            models_group, ["use", "openrouter", "fixture-lab/not-listed"]
        )

        assert result.exit_code != 0
        assert "BYOK_MODEL_NOT_LISTED" in result.output
        assert resolve_local_byok_credential_models("openrouter") == (_DEFAULT,)

    def test_use_with_no_key_stored_names_the_command_that_adds_one(self) -> None:
        result = CliRunner().invoke(models_group, ["use", "openrouter", _OTHER])

        assert result.exit_code != 0
        assert "onex models add openrouter" in result.output


# The falsifier for AC3: a model id spelled in the onboarding or BYOK code.
_MODEL_ID = re.compile(
    r"\b(?:openai|google|nvidia|qwen|anthropic|deepseek|meta-llama|mistralai)/[\w.:-]+"
    r"|claude-(?:sonnet|opus|haiku)[\w.-]*|gpt-[\w.-]+|gemini-[\w.-]+"
    r"|gemma-[\w.-]+|nemotron[\w.-]*"
)
_ROOT = Path(cli_secret.__file__).resolve().parents[1]
_ONBOARDING_BYOK_FILES = (
    "cli/cli_secret.py",
    "routing/byok_model_discovery.py",
    "routing/local_byok_route.py",
    "routing/byok_plan_detection.py",
    "inference/local_byok_credential_adapter.py",
    "projection/credential_publisher.py",
    "nodes/node_model_setup_effect/handlers/handler_model_setup.py",
    "nodes/node_local_secret_store_effect/handlers/handler_local_secret_store.py",
    "nodes/node_onboarding/handlers/handler_onboarding.py",
    "nodes/node_onboarding/contract.yaml",
    "nodes/node_llm_delegation_call_effect/handlers/handler_llm_delegation_call.py",
    "nodes/node_llm_delegation_call_effect/handlers/handler_inference_intent.py",
)


class TestNoModelIdInCode:
    def test_the_scan_finds_a_model_id_when_one_is_there(self) -> None:
        # Positive control: the pattern is not vacuous.
        for sample in (
            "google/some-model-it",
            "claude-sonnet-5-5",
            "gemini-2.5-flash",
            "nvidia/nemotron-x",
        ):
            assert _MODEL_ID.search(sample), sample

    @pytest.mark.parametrize("relative", _ONBOARDING_BYOK_FILES)
    def test_no_model_id_is_spelled_in_the_onboarding_or_byok_path(
        self, relative: str
    ) -> None:
        path = _ROOT / relative
        assert path.is_file(), path
        hits = _MODEL_ID.findall(path.read_text(encoding="utf-8"))
        assert hits == [], f"{relative} names a model id: {hits}"
