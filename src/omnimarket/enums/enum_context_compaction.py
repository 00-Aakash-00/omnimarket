# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT

"""Closed vocabularies of the compaction commands and receipt events (OMN-21112).

The receipt events carry no free text, so every class, reason and trigger a
receipt names is a member of one of these enums.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class EnumContextCompactionTrigger(StrEnum):
    """What started one compaction invocation."""

    MANUAL = "manual"
    AUTO = "auto"
    PRECOMPUTE = "precompute"


@unique
class EnumContextCompactionFailureClass(StrEnum):
    """Why an invocation fell back to native compaction before activation.

    The three ``source_*`` classes are the session-barrier outcomes; the rest are
    the context-pack builder's typed failures, carried through unchanged.
    """

    SOURCE_NOT_CAUGHT_UP = "source_not_caught_up"
    SOURCE_INCOMPLETE = "source_incomplete"
    SOURCE_DISABLED = "source_disabled"
    REQUIRED_FACTOR_MISSING = "required_factor_missing"
    PROFILE_NOT_FOUND = "profile_not_found"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    INVALID_PROFILE = "invalid_profile"
    ARTIFACT_HASH_MISMATCH = "artifact_hash_mismatch"


@unique
class EnumContextInstallUncertainReason(StrEnum):
    """Why an activation reached no ``installed`` or ``install_mismatch`` fact.

    ``native_compaction`` and ``not_selected`` are the two ``not_installed``
    reasons; ``not_installed`` is carried here rather than on a topic of its own.
    """

    NATIVE_COMPACTION = "native_compaction"
    NOT_SELECTED = "not_selected"
    CAPTURE_GAP = "capture_gap"
    UNPARSEABLE_RECORD = "unparseable_record"


@unique
class EnumContextCaptureStream(StrEnum):
    """The two capture streams a session barrier names a high-water for."""

    LINEAGE = "lineage"
    CONTENT = "content"


__all__ = [
    "EnumContextCaptureStream",
    "EnumContextCompactionFailureClass",
    "EnumContextCompactionTrigger",
    "EnumContextInstallUncertainReason",
]
