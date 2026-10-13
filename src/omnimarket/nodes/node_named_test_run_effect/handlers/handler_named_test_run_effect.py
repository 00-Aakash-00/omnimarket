# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""HandlerNamedTestRunEffect -- run the tests a delegation names against its answer (OMN-21077).

Canonical handler: ``handle(request: ModelNamedTestRunRequest) ->
ModelNamedTestRunResult``.

A code answer is judged by whether it works. The quality gate is a pure reducer
and runs nothing, so this effect runs the tests and the gate's
``passes_existing_tests`` check reads the ``ModelNamedTestRun`` it returns.

What it does, in order:

1. copies the workspace into a new temporary directory (the caller's directory
   is never written), leaving out version control, virtual environments and
   caches, and refusing a workspace over the declared size bounds;
2. writes the answer's code to ``answer_path`` inside the copy, refusing a
   path that resolves outside it;
3. runs each named pytest node id on its own, with this process's interpreter
   and the contract's declared arguments, in the copy, without a shell, under a
   per-test timeout;
4. removes the copy.

A named test fails when its run exits non-zero for any reason: an assertion, an
import or syntax error in the answer, no such test (pytest exit 5), or the
timeout. A refused run carries no ``ModelNamedTestRun``, so the gate treats it
as no tests named: never a pass.

The tests execute the delegated code on the host this handler runs on, with that
host's network. It runs only where the workspace is, which is the caller's own
host on the in-process delegation path, and only when the caller named tests.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import yaml
from omnibase_core.validators.no_unguarded_git_subprocess import (
    scrub_git_location_env,
)

from omnimarket.delegation.graded_ladder.graders import extract_code_block
from omnimarket.models.delegation.wire.model_named_test_run import ModelNamedTestRun
from omnimarket.models.delegation.wire.model_named_test_run_request import (
    EnumNamedTestRunStatus,
    ModelNamedTestRunRequest,
    ModelNamedTestRunResult,
)

logger = logging.getLogger(__name__)

_CONTRACT_PATH = Path(__file__).parent.parent / "contract.yaml"
_TIMEOUT_EXIT_CODE = 124
_OUTPUT_TAIL = 1500
_DETAIL_LIMIT = 4000


@lru_cache(maxsize=1)
def _declared_run_policy() -> dict[str, object]:
    raw = yaml.safe_load(_CONTRACT_PATH.read_text(encoding="utf-8"))
    policy = raw.get("named_test_run") if isinstance(raw, dict) else None
    if not isinstance(policy, dict):
        raise ValueError(f"{_CONTRACT_PATH} declares no named_test_run policy")
    return policy


def _declared_test_args() -> tuple[str, ...]:
    args = _declared_run_policy().get("test_args")
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("named_test_run.test_args must be a list of strings")
    return tuple(args)


def _declared_int(key: str) -> int:
    value = _declared_run_policy().get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"named_test_run.{key} must be a positive integer")
    return value


def _declared_excluded_names() -> frozenset[str]:
    names = _declared_run_policy().get("excluded_names")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("named_test_run.excluded_names must be a list of strings")
    return frozenset(names)


#: The only caller variables the tests see. The tests execute model-written code,
#: so the caller's credentials and tokens in its environment are not passed on.
_PASSED_ENV_NAMES = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")


def _test_env() -> dict[str, str]:
    """A minimal environment: no caller secrets, git location, virtualenv or bytecode."""
    env = scrub_git_location_env(
        {name: os.environ[name] for name in _PASSED_ENV_NAMES if name in os.environ}
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


class _WorkspaceRefusedError(Exception):
    """The workspace or answer path cannot be used; the run is refused."""


class HandlerNamedTestRunEffect:
    """EFFECT handler: run named tests against a delegated answer in a workspace copy."""

    def __init__(
        self,
        *,
        interpreter: str | None = None,
        test_args: tuple[str, ...] | None = None,
    ) -> None:
        self._interpreter = interpreter or sys.executable
        self._test_args = test_args if test_args is not None else _declared_test_args()

    def handle(self, request: ModelNamedTestRunRequest) -> ModelNamedTestRunResult:
        spec = request.spec
        workspace = Path(spec.workspace_root)
        if not workspace.is_dir():
            return _refused(f"workspace {spec.workspace_root} is not a directory")
        scratch = Path(tempfile.mkdtemp(prefix="onex-named-tests-"))
        try:
            copy = scratch / "workspace"
            try:
                _copy_workspace(workspace, copy)
                _write_answer(copy, spec.answer_path, request.answer_content)
            except _WorkspaceRefusedError as exc:
                return _refused(str(exc))
            failed: list[str] = []
            first_failure_code = 0
            notes: list[str] = []
            for test_id in spec.test_ids:
                code, output = self._run_one(copy, test_id, request.timeout_seconds)
                if code != 0:
                    failed.append(test_id)
                    first_failure_code = first_failure_code or code
                    notes.append(f"{test_id} exit {code}: {output[-_OUTPUT_TAIL:]}")
            run = ModelNamedTestRun(
                test_ids=spec.test_ids,
                failed_test_ids=tuple(failed),
                exit_code=first_failure_code,
            )
            summary = (
                f"{len(spec.test_ids) - len(failed)} of {len(spec.test_ids)} named "
                f"test(s) passed against {spec.answer_path}"
            )
            logger.info("named test run: %s", summary)
            return ModelNamedTestRunResult(
                status=EnumNamedTestRunStatus.COMPLETED,
                run=run,
                detail="\n".join([summary, *notes])[:_DETAIL_LIMIT],
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _run_one(
        self, cwd: Path, test_id: str, timeout_seconds: int
    ) -> tuple[int, str]:
        argv = [self._interpreter, *self._test_args, test_id]
        started = time.monotonic()
        try:
            done = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=_test_env(),
            )
        except subprocess.TimeoutExpired:
            return _TIMEOUT_EXIT_CODE, f"timed out after {timeout_seconds} s"
        logger.debug(
            "named test %s exit %d in %.1f s",
            test_id,
            done.returncode,
            time.monotonic() - started,
        )
        return done.returncode, done.stdout + done.stderr


def _refused(detail: str) -> ModelNamedTestRunResult:
    logger.warning("named test run refused: %s", detail)
    return ModelNamedTestRunResult(
        status=EnumNamedTestRunStatus.REFUSED, detail=detail[:_DETAIL_LIMIT]
    )


def _copy_workspace(source: Path, destination: Path) -> None:
    """Copy the workspace, leaving out declared names, within the declared bounds."""
    excluded = _declared_excluded_names()
    max_files = _declared_int("max_workspace_files")
    max_bytes = _declared_int("max_workspace_bytes")
    files = 0
    size = 0
    for root, dirs, names in os.walk(source):
        dirs[:] = [d for d in dirs if d not in excluded]
        for name in names:
            if name in excluded:
                continue
            files += 1
            path = Path(root) / name
            if not path.is_symlink():
                size += path.stat().st_size
            if files > max_files or size > max_bytes:
                raise _WorkspaceRefusedError(
                    f"workspace {source} exceeds the declared bounds "
                    f"({max_files} files, {max_bytes} bytes)"
                )
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(*sorted(excluded)),
    )


def _write_answer(copy: Path, answer_path: str, answer_content: str) -> None:
    root = copy.resolve()
    unresolved = copy / answer_path
    if unresolved.is_symlink() or unresolved.is_dir():
        raise _WorkspaceRefusedError(
            f"answer_path {answer_path} is a symlink or a directory"
        )
    # Resolving follows a symlinked parent, so a path that leaves the copy
    # through one is caught here, before anything is written.
    target = unresolved.resolve()
    if root not in target.parents:
        raise _WorkspaceRefusedError(
            f"answer_path {answer_path} resolves outside the workspace"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        extract_code_block(answer_content).rstrip() + "\n", encoding="utf-8"
    )


__all__ = ["HandlerNamedTestRunEffect"]
