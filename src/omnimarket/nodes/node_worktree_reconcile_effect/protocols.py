# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Injected boundaries; no handler constructs its own collaborators."""

import re
from datetime import datetime
from typing import ClassVar, Protocol

from pydantic import BaseModel

from omnimarket.events.worktree_reconcile import (
    ModelWorktreeDecisionRecord,
    ModelWorktreeFacts,
    ModelWorktreeReconcileCommand,
)

_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"<>]+")


class WorktreeRemoveFailedError(RuntimeError):
    """A non-zero git worktree remove, carrying git's own error line."""

    label: ClassVar[str] = "remove_failed"

    def __init__(self, *, exit_code: int, git_line: str) -> None:
        self.exit_code = exit_code
        # stderr may carry credential-bearing URLs.
        self.git_line = _URL.sub("<url>", git_line)
        super().__init__(f"{self.label} (exit {exit_code}): {self.git_line}")


class ProtocolWorktreeFactsProbe(Protocol):
    def discover(
        self, command: ModelWorktreeReconcileCommand, now: datetime
    ) -> tuple[ModelWorktreeFacts, ...]: ...
    def revalidate(
        self, facts: ModelWorktreeFacts, now: datetime
    ) -> ModelWorktreeFacts: ...


class ProtocolWorktreeDecider(Protocol):
    def decide(
        self, argv: list[str], facts: tuple[ModelWorktreeFacts, ...]
    ) -> tuple[ModelWorktreeDecisionRecord, ...]: ...


class ProtocolWorktreePinner(Protocol):
    def pin(self, argv: list[str], facts: ModelWorktreeFacts) -> bool: ...


class ProtocolWorktreeRemover(Protocol):
    def remove(self, facts: ModelWorktreeFacts, now: datetime) -> None: ...


class ProtocolClock(Protocol):
    def now(self) -> datetime: ...


class ProtocolWorktreeEventPublisher(Protocol):
    def publish(self, topic: str, event: BaseModel) -> None: ...
