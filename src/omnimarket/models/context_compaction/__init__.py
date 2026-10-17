# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Compaction command and receipt event schemas (OMN-21112)."""

from __future__ import annotations

from pydantic import BaseModel

from omnimarket.models.context_compaction.model_context_compaction_command import (
    ModelContextCompactionMessage,
    ModelContextCompactionPackage,
    ModelContextCompactionRequested,
)
from omnimarket.models.context_compaction.model_context_compaction_receipt import (
    ModelContextCompactionActivated,
    ModelContextCompactionFallback,
    ModelContextCompactionInstalled,
    ModelContextCompactionInstallMismatch,
    ModelContextCompactionInstallUncertain,
    ModelContextCompactionPrepared,
)

COMPACTION_COMMAND_MODELS: tuple[type[BaseModel], ...] = (
    ModelContextCompactionRequested,
    ModelContextCompactionPackage,
)
COMPACTION_RECEIPT_MODELS: tuple[type[BaseModel], ...] = (
    ModelContextCompactionPrepared,
    ModelContextCompactionActivated,
    ModelContextCompactionFallback,
    ModelContextCompactionInstalled,
    ModelContextCompactionInstallMismatch,
    ModelContextCompactionInstallUncertain,
)

__all__ = [
    "COMPACTION_COMMAND_MODELS",
    "COMPACTION_RECEIPT_MODELS",
    "ModelContextCompactionActivated",
    "ModelContextCompactionFallback",
    "ModelContextCompactionInstallMismatch",
    "ModelContextCompactionInstallUncertain",
    "ModelContextCompactionInstalled",
    "ModelContextCompactionMessage",
    "ModelContextCompactionPackage",
    "ModelContextCompactionPrepared",
    "ModelContextCompactionRequested",
]
