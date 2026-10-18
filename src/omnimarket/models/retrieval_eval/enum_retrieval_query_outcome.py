# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Retrieval runner outcomes (OMN-21132, plan task TC.9, port item AP-E1)."""

from enum import StrEnum


class EnumRetrievalQueryOutcome(StrEnum):
    """A failed runner still contributes a query to evaluation denominators."""

    OK = "ok"
    FAILED = "failed"
