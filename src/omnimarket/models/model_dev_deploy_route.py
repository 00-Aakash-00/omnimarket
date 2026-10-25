# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Host-neutral health and immutable dev deploy/verify selection (OMN-21402)."""

from __future__ import annotations

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool


class ModelDevDeployVerifyTargets(BaseModel):
    """The proof surface advertised with an instance's deployment capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_lane: str = Field(min_length=1)
    runner_labels: tuple[str, ...] = Field(min_length=1)
    deploy_agent_url: str = Field(min_length=1)
    main_url: str = Field(min_length=1)
    effects_url: str = Field(min_length=1)
    projection_url: str = Field(min_length=1)
    compose_project: str = Field(min_length=1)
    runtime_container: str = Field(min_length=1)
    effects_container: str = Field(min_length=1)
    postgres_container: str = Field(min_length=1)
    broker_container: str = Field(min_length=1)


class ModelDevDeployHealth(BaseModel):
    """An agent's heartbeat, including whether its verify surface is ready.

    Only a heartbeat producer may assert these facts. A tag-lag reading or a
    runtime HTTP 200 alone does not establish an agent's deploy capability.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    observed_at: AwareDatetime
    accepting: StrictBool
    verify_ready: StrictBool
    priority: int = Field(default=0, strict=True, ge=0)
    verify: ModelDevDeployVerifyTargets


class ModelDevDeployRoute(BaseModel):
    """One decision shared by deployment and verification, never reselected.

    The producer signs this as part of the rebuild command. Consumers compare
    their own instance to ``instance``; CI uses ``verify`` from this same record.
    They must not independently read live health or recompute a table route.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    selected_at: AwareDatetime
    heartbeat_at: AwareDatetime
    verify: ModelDevDeployVerifyTargets

    def accepts(self, instance: str) -> bool:
        """The sole accepting instance, including after health changes."""
        return instance == self.instance

    def verify_outputs(self) -> dict[str, str]:
        """CI job placement derived from the selected command, not new health."""
        import json

        return {
            "routed_instance": self.instance,
            "receipt_lane": self.verify.receipt_lane,
            "verify_runs_on": json.dumps(list(self.verify.runner_labels)),
        }


class ModelDevDeployRoutingPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    heartbeat_max_age_seconds: int = Field(strict=True, gt=0, le=300)
    max_health_rows: int = Field(strict=True, gt=0)
    max_decisions: int = Field(strict=True, gt=0)


def select_dev_deploy_route(
    readings: tuple[ModelDevDeployHealth, ...],
    *,
    repository: str,
    now: datetime,
    policy: ModelDevDeployRoutingPolicy,
) -> ModelDevDeployRoute:
    """Choose one currently eligible instance; no stale or guessed fallback.

    Membership comes from the heartbeat stream. Priority is a deployment fact,
    and instance is the deterministic tie-breaker, not a name embedded in code.
    Future-dated heartbeats refuse eligibility rather than extending a lease.
    """
    if now.utcoffset() is None:
        raise ValueError("dev deploy selection requires an aware timestamp")
    candidates = [
        reading
        for reading in readings
        if reading.repository == repository
        and reading.accepting
        and reading.verify_ready
        and 0
        <= (now - reading.observed_at).total_seconds()
        < policy.heartbeat_max_age_seconds
    ]
    if not candidates:
        raise ValueError(f"no fresh deploy-and-verify heartbeat for {repository!r}")
    selected = min(candidates, key=lambda row: (row.priority, row.instance))
    return ModelDevDeployRoute(
        instance=selected.instance,
        repository=repository,
        selected_at=now,
        heartbeat_at=selected.observed_at,
        verify=selected.verify,
    )
