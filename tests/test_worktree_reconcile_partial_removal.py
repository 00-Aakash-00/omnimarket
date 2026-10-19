# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Partial linked removal, refusal evidence and discovery of broken pointers."""

import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omnimarket.events.worktree_reconcile import EnumWorktreeKind as Kind
from omnimarket.events.worktree_reconcile import ModelWorktreeFacts
from omnimarket.nodes.node_worktree_reconcile_effect.handlers import adapter_remover
from omnimarket.nodes.node_worktree_reconcile_effect.handlers.adapter_facts import (
    GitWorktreeFactsProbe,
)
from tests.test_worktree_reconcile_compute import facts
from tests.test_worktree_reconcile_effect import (
    NOW,
    Fakes,
    command,
    git_fixture,
    run_git,
)

pytestmark = pytest.mark.unit


def _committed_worktree(tmp_path: Path) -> tuple[ModelWorktreeFacts, Path]:
    clone, root, _ = git_fixture(tmp_path)
    path = root / "task" / "repo"
    run_git(clone, "worktree", "add", "-b", "feature", str(path))
    (path / "sub").mkdir()
    (path / "sub" / "file").write_text("committed\n")
    run_git(path, "add", "sub/file")
    run_git(path, "commit", "-m", "subdirectory")
    admin = Path(run_git(path, "rev-parse", "--absolute-git-dir"))
    return facts(kind=Kind.LINKED_WORKTREE, path=str(path), root=str(root)), admin


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory write modes")
def test_real_partial_removal(tmp_path: Path) -> None:
    fact, admin = _committed_worktree(tmp_path)
    path = Path(fact.path)
    sub = path / "sub"
    sub.chmod(0o500)
    try:
        with pytest.raises(adapter_remover.WorktreePartiallyRemovedError) as caught:
            adapter_remover.GitWorktreeRemover().remove(fact, NOW)
        assert caught.value.exit_code != 0
        assert "failed to delete" in caught.value.git_line
        assert str(caught.value).startswith("partially_removed (exit ")
        assert path.exists()
        assert not admin.exists()
    finally:
        sub.chmod(0o700)


def test_deletable_worktree(tmp_path: Path) -> None:
    fact, admin = _committed_worktree(tmp_path)
    path = Path(fact.path)
    adapter_remover.GitWorktreeRemover().remove(fact, NOW)
    assert not path.exists()
    assert not admin.exists()
    assert not path.parent.exists()


def test_refused_worktree(tmp_path: Path) -> None:
    fact, admin = _committed_worktree(tmp_path)
    path = Path(fact.path)
    (path / "untracked").write_text("unsaved\n")
    with pytest.raises(adapter_remover.WorktreeRemoveRefusedError) as caught:
        adapter_remover.GitWorktreeRemover().remove(fact, NOW)
    assert caught.value.exit_code != 0
    assert caught.value.git_line.startswith("fatal:")
    assert str(caught.value).startswith("remove_refused (exit ")
    assert path.exists()
    assert admin.exists()


@pytest.mark.parametrize(
    ("exception_name", "git_line", "expected"),
    [
        (
            "WorktreePartiallyRemovedError",
            "error: failed to delete 'x': Permission denied",
            "partially_removed (exit 255): error: failed to delete",
        ),
        (
            "WorktreeRemoveRefusedError",
            "fatal: contains modified or untracked files",
            "remove_refused (exit 255): fatal:",
        ),
        ("ValueError", "private detail", "ValueError"),
    ],
)
def test_handler_records_failed_attempt(
    monkeypatch: pytest.MonkeyPatch,
    exception_name: str,
    git_line: str,
    expected: str,
) -> None:
    head = "a" * 40
    fake = Fakes((facts(head_on_remote=True, head_sha=head),))
    exc = (
        ValueError(git_line)
        if exception_name == "ValueError"
        else getattr(adapter_remover, exception_name)(exit_code=255, git_line=git_line)
    )

    def fail(fact: ModelWorktreeFacts, now: datetime) -> None:
        raise exc

    monkeypatch.setattr(fake, "remove", fail)
    result = fake.handler().handle(command())
    event = result.decided_events[0]
    assert event.outcome == "failed"
    assert event.error is not None
    assert event.error.startswith(expected)
    assert event.facts.head_sha == head


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "fatal: other failure\nerror: failed to delete 'https://user:tok@example.invalid/x': Permission denied\n",
            "error: failed to delete '<url>': Permission denied",
        ),
        (
            "warning: ignored\nfatal: https://user:tok@example.invalid/x unavailable\nerror: later\n",
            "fatal: <url> unavailable",
        ),
        (
            "error: https://user:tok@example.invalid/x and ssh://user:tok@example.invalid/y\n",
            "error: <url> and <url>",
        ),
        ("warning: https://user:tok@example.invalid/x\n", ""),
    ],
)
def test_stderr_selection_and_url_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stderr: str, expected: str
) -> None:
    fact, _ = _committed_worktree(tmp_path)
    real_run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def refuse(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[3:5] == ["worktree", "remove"]:
            assert argv == [
                "git",
                "-C",
                str(Path(fact.path).parents[2] / "canonical" / ".git"),
                "worktree",
                "remove",
                fact.path,
            ]
            assert kwargs["capture_output"] is True
            assert kwargs["text"] is True
            assert kwargs["timeout"] == 60
            assert kwargs["check"] is False
            return subprocess.CompletedProcess(argv, 255, stdout="", stderr=stderr)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(adapter_remover.WorktreeRemoveRefusedError) as caught:
        adapter_remover.GitWorktreeRemover().remove(fact, NOW)
    assert caught.value.exit_code == 255
    assert caught.value.git_line == expected
    assert "user:tok" not in str(caught.value)


@pytest.mark.parametrize("relative", [False, True])
def test_discovery_reports_half_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool
) -> None:
    clone, root, _ = git_fixture(tmp_path)
    clean, partial = root / "clean" / "repo", root / "partial" / "repo"
    run_git(clone, "worktree", "add", "-b", "clean", str(clean))
    run_git(clone, "worktree", "add", "-b", "partial", str(partial))
    admin = Path(run_git(partial, "rev-parse", "--absolute-git-dir"))
    if relative:
        (partial / ".git").write_text(f"gitdir: {os.path.relpath(admin, partial)}\n")
    shutil.rmtree(admin)
    monkeypatch.setattr(
        "omnimarket.nodes.node_worktree_reconcile_effect.handlers.adapter_facts.process_cwds",
        lambda: (),
    )
    fake = Fakes(())
    monkeypatch.setattr(fake, "now", lambda: datetime(2100, 1, 1, tzinfo=UTC))
    real_run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def spy(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[:3] != ["git", "-C", str(partial)]
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    found = GitWorktreeFactsProbe().discover(command(roots=(str(root),)), fake.now())
    by_path = {item.path: item for item in found}
    assert by_path[str(partial)].kind == Kind.LINKED_WORKTREE
    assert by_path[str(partial)].probe_errors == ("half_removed",)
    assert "half_removed" not in by_path[str(clean)].probe_errors
    assert by_path[str(clean)].facts_complete
