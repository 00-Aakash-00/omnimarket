# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Shared accepted-finding, signature, recurrence-state and candidate wire types."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

ReviewFamily = Literal[
    "architecture",
    "security",
    "performance",
    "correctness",
    "completeness",
    "feasibility",
    "testing",
    "style",
]
MINHASH_SIZE = 128


class ModelReviewFindingUncoded(BaseModel):
    """One X finding from a final accepted voter verdict, never an attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: UUID
    finding_id: str = Field(min_length=1)
    review_run_id: str = Field(min_length=1)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    pr_number: int = Field(gt=0)
    path: str = Field(min_length=1)
    family: ReviewFamily
    note: str = Field(min_length=1, max_length=120)
    voter: str = Field(min_length=1)
    observed_at: AwareDatetime
    defect_code: Literal["X"] = "X"
    accepted: Literal[True] = True


class ModelReviewFindingSignature(ModelReviewFindingUncoded):
    """Versioned deterministic signature and seeded MinHash of content tokens."""

    signature_version: Literal["v1"] = "v1"
    normalized_note: str
    content_tokens: tuple[str, ...] = Field(min_length=1)
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    minhash: tuple[int, ...] = Field(min_length=MINHASH_SIZE, max_length=MINHASH_SIZE)


class ModelReviewSignaturePolicy(BaseModel):
    """The reducer's contract-owned recurrence thresholds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    similarity_threshold: float = Field(default=0.8, gt=0, le=1)
    distinct_prs: int = Field(default=3, ge=2)
    window_days: int = Field(default=14, ge=1)


class ModelReviewSignatureExample(BaseModel):
    """A proposal example references a finding; it contains no source text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signature: str
    repository: str
    pr_number: int
    finding_id: str
    observed_at: AwareDatetime


class ModelReviewSignatureGroup(BaseModel):
    """Near-duplicate family with retained sketches and a permanent proposal mark."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: str
    family: ReviewFamily
    # One sketch per exact signature remains after observations age out, so an
    # already-proposed family cannot be proposed again when the window changes.
    representatives: tuple[ModelReviewFindingSignature, ...]
    observations: tuple[ModelReviewSignatureExample, ...] = ()
    proposed_id: str | None = None


class ModelReviewUncodedState(BaseModel):
    """A serializable family partition, persisted with its proposal outbox."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: ReviewFamily
    watermark: AwareDatetime | None = None
    groups: tuple[ModelReviewSignatureGroup, ...] = ()


class ModelReviewCodeCandidateProposed(BaseModel):
    """One stable catalog proposal request; downstream effects dedupe candidate_id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: UUID
    candidate_id: str
    family: ReviewFamily
    group_signature: str
    content_tokens: tuple[str, ...]
    pr_refs: tuple[str, ...]
    examples: tuple[ModelReviewSignatureExample, ...]
    window_start: AwareDatetime
    window_end: AwareDatetime
