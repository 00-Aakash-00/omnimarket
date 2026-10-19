# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Pure, versioned normalization and MinHash; no model calls, clock or I/O."""

from __future__ import annotations

import hashlib
import json
import re

from omnimarket.models.model_review_finding_signature import (
    MINHASH_SIZE,
    ModelReviewFindingSignature,
    ModelReviewFindingUncoded,
)

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "was",
        "were",
        "when",
        "where",
        "which",
        "while",
        "with",
        "would",
        "should",
        "could",
        "line",
    ]
)
_PLACEHOLDERS = frozenset({"identifier", "path", "number", "string"})
_BACKTICKS = re.compile(r"`+[^`]*`+")
_QUOTED = re.compile(r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' """, re.VERBOSE)
# Match repository/absolute paths (including :line anchors), Windows paths and
# bare source filenames. Mask before numbers, so a path's line number stays out.
_PATHS = re.compile(
    r"(?:[a-z]:[\\/]|\.{0,2}/)?(?:[\w.-]+[\\/])+[\w./\\-]+(?::\d+)?"
    r"|\b[\w-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|yaml|yml|json|toml|sql|md)(?::\d+)?\b"
)
_NUMBERS = re.compile(r"\b(?:0x[0-9a-f]+|\d+(?:\.\d+)*)\b")


def _stem(token: str) -> str:
    """Conservative English suffix stem, versioned with the signature algorithm."""
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    for suffix in ("ing", "ed"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            root = token[: -len(suffix)]
            if len(root) > 2 and root[-1] == root[-2] and root[-1] not in "lsz":
                root = root[:-1]
            return root
    if len(token) > 4 and token.endswith(("sses", "ches", "shes", "xes")):
        return token[:-2]
    if (
        len(token) > 3
        and token.endswith("s")
        and not token.endswith(("ss", "us", "is"))
    ):
        return token[:-1]
    return token


class HandlerReviewFindingSignatureCompute:
    """Accepted X finding -> deterministic signature plus 128 seeded minima."""

    def handle(self, request: ModelReviewFindingUncoded) -> ModelReviewFindingSignature:
        note = request.note.lower()
        for pattern, placeholder in (
            (_BACKTICKS, "identifier"),
            (_QUOTED, "string"),
            (_PATHS, "path"),
            (_NUMBERS, "number"),
        ):
            note = pattern.sub(f" <{placeholder}> ", note)
        normalized = " ".join(note.split())
        tokens = tuple(
            sorted(
                {
                    _stem(token)
                    for token in re.findall(r"[a-z]+", normalized)
                    if token not in _STOPWORDS and token not in _PLACEHOLDERS
                }
            )
        )
        if not tokens:
            raise ValueError("uncoded finding has no content tokens after masking")
        encoded = json.dumps(["v1", request.family, tokens], separators=(",", ":"))
        signature = hashlib.sha256(encoded.encode()).hexdigest()
        # Independent fixed seeds; byte order and hash algorithm are part of v1.
        sketch = tuple(
            min(
                int.from_bytes(
                    hashlib.sha256(seed.to_bytes(4, "big") + token.encode()).digest()[
                        :8
                    ],
                    "big",
                )
                for token in tokens
            )
            for seed in range(MINHASH_SIZE)
        )
        return ModelReviewFindingSignature(
            **request.model_dump(),
            normalized_note=normalized,
            content_tokens=tokens,
            signature=signature,
            minhash=sketch,
        )
