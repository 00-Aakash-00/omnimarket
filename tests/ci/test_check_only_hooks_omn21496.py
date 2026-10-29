# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""OMN-21496 T2.6: no pre-commit hook of this repository writes a file.

A hook that rewrites a file or a commit message hides a defect from the author
and breaks the rule that a hook run leaves the working tree byte-identical.
The hooks here refuse instead: ``ruff format --check``, ``ruff check
--no-fix``, an end-of-file check that does not fix, and a refusal of the AI
co-author trailer where the old hook stripped it.

Two layers: the declared hooks (no writing flag, no writing entry, the
replacement ids exist) and the behaviour of each replacement on a canary file
and a canary commit message (refused, bytes unchanged).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from omnibase_core.validators.no_unguarded_git_subprocess import scrub_git_location_env

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"

REPLACED_IDS = frozenset({"end-of-file-fixer", "strip-ai-coauthor-trailer"})
EOF_CHECK_ID = "end-of-file-check"
TRAILER_CHECK_ID = "refuse-ai-coauthor-trailer"

# Flags and entry fragments that make a hook write to the tree or the message.
WRITING_ARGS = frozenset({"--fix", "--write", "--in-place", "--unsafe-fixes"})
WRITING_ENTRY = re.compile(
    r"\bperl\s+-\w*i|\bsed\s+-\w*i|\brm\s+-\w*r|--fix\b|--write\b"
)

CANARY_UNFORMATTED = b"import os\nx=1\nif x :\n  print( x )\n"
CANARY_TRAILER = b"fix: a change\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"


def _hooks() -> dict[str, dict[str, Any]]:
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    hooks: dict[str, dict[str, Any]] = {}
    for repo in config["repos"]:
        for hook in repo["hooks"]:
            hooks[hook["id"]] = hook
    return hooks


def _clean_env() -> dict[str, str]:
    return scrub_git_location_env(os.environ)


def _git_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", str(path)],
        check=True,
        env=scrub_git_location_env(os.environ),
    )


def _run_hook(
    tmp_path: Path, hook: dict[str, Any], *extra: str
) -> subprocess.CompletedProcess[str]:
    """Run one declared local hook through pre-commit in a scratch git repo."""
    _git_repo(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"repos": [{"repo": "local", "hooks": [hook]}]}))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pre_commit",
            "run",
            hook["id"],
            "--config",
            str(config),
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_clean_env(),
        check=False,
    )


def test_the_writing_hooks_are_gone() -> None:
    assert REPLACED_IDS.isdisjoint(_hooks())


def test_no_hook_declares_a_writing_flag_or_entry() -> None:
    offenders = []
    for hook_id, hook in _hooks().items():
        args = set(hook.get("args", []))
        entry = hook.get("entry", "")
        if args & WRITING_ARGS or WRITING_ENTRY.search(entry):
            offenders.append(hook_id)
    assert offenders == []


def test_ruff_hooks_are_check_only() -> None:
    hooks = _hooks()
    assert "--check" in hooks["ruff-format"]["args"]
    assert "--no-fix" in hooks["ruff"]["args"]
    assert "--exit-non-zero-on-fix" not in hooks["ruff"]["args"]


def test_replacements_are_declared() -> None:
    hooks = _hooks()
    assert hooks[EOF_CHECK_ID]["language"] == "pygrep"
    assert hooks[TRAILER_CHECK_ID]["language"] == "pygrep"
    assert hooks[TRAILER_CHECK_ID]["stages"] == ["commit-msg"]


@pytest.mark.parametrize(
    ("hook_id", "subcommand"), [("ruff-format", "format"), ("ruff", "check")]
)
def test_ruff_args_refuse_an_unformatted_file_and_leave_it_alone(
    tmp_path: Path, hook_id: str, subcommand: str
) -> None:
    ruff = shutil.which("ruff") or str(Path(sys.executable).with_name("ruff"))
    target = tmp_path / "canary.py"
    target.write_bytes(CANARY_UNFORMATTED)
    args = _hooks()[hook_id].get("args", [])
    result = subprocess.run(
        [ruff, subcommand, *args, "--isolated", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert target.read_bytes() == CANARY_UNFORMATTED


@pytest.mark.parametrize(
    "content",
    [b"no final newline", b"two final newlines\n\n", b"three\n\n\n"],
    ids=["missing", "double", "triple"],
)
def test_end_of_file_check_refuses_and_leaves_the_file_alone(
    tmp_path: Path, content: bytes
) -> None:
    hook = dict(_hooks()[EOF_CHECK_ID])
    target = tmp_path / "canary.txt"
    target.write_bytes(content)
    result = _run_hook(tmp_path, hook, "--files", str(target))
    assert result.returncode != 0, result.stdout + result.stderr
    assert target.read_bytes() == content


def test_end_of_file_check_passes_a_file_with_one_final_newline(
    tmp_path: Path,
) -> None:
    hook = dict(_hooks()[EOF_CHECK_ID])
    target = tmp_path / "clean.txt"
    target.write_bytes(b"one line\n")
    result = _run_hook(tmp_path, hook, "--files", str(target))
    assert result.returncode == 0, result.stdout + result.stderr


def test_trailer_check_refuses_and_leaves_the_message_alone(tmp_path: Path) -> None:
    hook = dict(_hooks()[TRAILER_CHECK_ID])
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_bytes(CANARY_TRAILER)
    result = _run_hook(
        tmp_path,
        hook,
        "--hook-stage",
        "commit-msg",
        "--commit-msg-filename",
        str(message),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert message.read_bytes() == CANARY_TRAILER


def test_trailer_check_passes_a_message_without_the_trailer(tmp_path: Path) -> None:
    hook = dict(_hooks()[TRAILER_CHECK_ID])
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_bytes(b"fix: a change\n\nCo-Authored-By: A Person <a@example.org>\n")
    result = _run_hook(
        tmp_path,
        hook,
        "--hook-stage",
        "commit-msg",
        "--commit-msg-filename",
        str(message),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _tree_digest(root: Path) -> dict[str, str]:
    """Hash the canary tree, excluding git internals and generated hook config."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(root).parts
        and path.relative_to(root).as_posix() != "config.yaml"
    }


def test_canary_commit_is_refused_and_the_whole_tree_is_byte_identical(
    tmp_path: Path,
) -> None:
    _git_repo(tmp_path)
    source = tmp_path / "src" / "canary.py"
    source.parent.mkdir()
    source.write_bytes(CANARY_UNFORMATTED)
    (tmp_path / "notes.txt").write_bytes(b"no final newline")
    (tmp_path / "COMMIT_EDITMSG").write_bytes(CANARY_TRAILER)
    before = _tree_digest(tmp_path)

    hooks = _hooks()
    ruff = shutil.which("ruff") or str(Path(sys.executable).with_name("ruff"))
    for hook_id, subcommand in [("ruff-format", "format"), ("ruff", "check")]:
        result = subprocess.run(
            [
                ruff,
                subcommand,
                *hooks[hook_id].get("args", []),
                "--isolated",
                str(source),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0, result.stdout + result.stderr

    result = _run_hook(tmp_path, dict(hooks[EOF_CHECK_ID]), "--files", "notes.txt")
    assert result.returncode != 0, result.stdout + result.stderr
    result = _run_hook(
        tmp_path,
        dict(hooks[TRAILER_CHECK_ID]),
        "--hook-stage",
        "commit-msg",
        "--commit-msg-filename",
        "COMMIT_EDITMSG",
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert _tree_digest(tmp_path) == before
