"""Request model for deterministic context-pack assembly."""

from __future__ import annotations

from omnibase_core.enums.enum_context_factor import EnumContextFactor
from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnimarket.models.context_pack.model_context_assembly_identity import (
    ModelContextAssemblyIdentity,
)
from omnimarket.pack.model_context_pack_artifact import (
    ModelContextPackArtifact,
)
from omnimarket.pack.model_context_profile import (
    ModelContextProfile,
)


class ModelContextPackBuilderRequest(BaseModel):
    """All context-pack inputs, already resolved by upstream effects."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_hash: str = Field(min_length=1)
    generated_at: str = Field(
        ...,
        min_length=1,
        description="Timezone-aware ISO8601 UTC timestamp supplied by caller.",
    )
    profile: ModelContextProfile
    artifacts: tuple[ModelContextPackArtifact, ...] = Field(default_factory=tuple)
    assembly_identity: ModelContextAssemblyIdentity | None = None

    @model_validator(mode="after")
    def _validate_item_mode(self) -> ModelContextPackBuilderRequest:
        if self.profile.item_level_exemplars:
            if self.assembly_identity is None:
                raise ValueError("item-level mode requires assembly_identity")
            for artifact in self.artifacts:
                if artifact.factor is EnumContextFactor.EXEMPLAR and (
                    artifact.effectiveness_score is None or artifact.confidence is None
                ):
                    raise ValueError(
                        "item-level exemplars require effectiveness_score and confidence"
                    )
        return self


__all__ = ["ModelContextPackBuilderRequest"]
