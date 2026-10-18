# SPDX-License-Identifier: MIT
"""Repository contract extensions consumed by the DoD verifier."""

from __future__ import annotations

from omnibase_core.models.ticket.model_contract_dod_item import ModelContractDodItem
from pydantic import Field


class ModelRepoContractDodItem(ModelContractDodItem):
    """The core execution schema plus this verifier's criterion binding records.

    The existing criteria and retirement services validate the records. Declaring
    their fields here makes the accepted item shape independent of a checkout of
    another repository while retaining the core model's extra-field refusal.
    """

    ac_bindings: tuple[dict[str, object], ...] = Field(default=())
    supersedes_ac_binding: tuple[dict[str, object], ...] = Field(default=())
