# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Pure authority checks against actual packed chunks, matched by item identity."""

from omnibase_core.enums.enum_context_factor import EnumContextFactor
from omnibase_core.models.pack.model_context_pack import ModelContextPack

from omnimarket.models.context_pack.model_context_pack_fidelity_verdict import (
    ModelContextPackFidelityVerdict,
)
from omnimarket.pack.model_context_pack_artifact import ModelContextPackArtifact


def check_authority_fidelity(
    originals: tuple[ModelContextPackArtifact, ...],
    pack: ModelContextPack,
    required_factors: tuple[EnumContextFactor, ...],
) -> ModelContextPackFidelityVerdict:
    """Refuse missing, duplicated, shortened or rewritten authority items.

    Callers pass the originals after declared content deduplication. Comparing
    whole text also catches the Archive's 26-38 character truncation defect
    without rejecting legitimate short rulings through an arbitrary length floor.
    """
    required = set(required_factors)
    packed: dict[tuple[EnumContextFactor, str], list[str]] = {}
    for chunk in pack.chunks:
        if chunk.factor in required:
            packed.setdefault((chunk.factor, chunk.source_artifact_hash), []).append(
                chunk.content
            )
    expected = {
        (artifact.factor, artifact.source_artifact_hash): artifact.content
        for artifact in originals
        if artifact.factor in required
    }
    violations = [
        f"authority fidelity failed: {factor.value}:{ident}"
        for (factor, ident), content in expected.items()
        if packed.get((factor, ident)) != [content]
    ]
    violations.extend(
        f"unrecognised authority item: {factor.value}:{ident}"
        for factor, ident in packed.keys() - expected.keys()
    )
    return ModelContextPackFidelityVerdict(violations=tuple(sorted(violations)))
