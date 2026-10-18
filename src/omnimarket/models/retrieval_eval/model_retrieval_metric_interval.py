# SPDX-FileCopyrightText: 2025 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Deterministic bootstrap interval (OMN-21132, TC.9, AP-E1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ModelRetrievalMetricInterval(BaseModel):
    """Mean and empirical percentile bounds with reproducibility settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mean: float
    lower: float
    upper: float
    confidence: float
    method: Literal["bootstrap-percentile"]
    resamples: int
    seed: int
