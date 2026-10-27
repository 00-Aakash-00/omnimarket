# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Full assembly digest over deterministic content and its pinned input versions."""

import hashlib
import json

from omnibase_core.enums.enum_context_factor import EnumContextFactor
from omnibase_core.models.pack.model_context_pack import ModelContextPack

from omnimarket.models.context_pack.model_context_assembly_identity import (
    ModelContextAssemblyIdentity,
)


def compute_context_assembly_hash(
    pack: ModelContextPack, identity: ModelContextAssemblyIdentity | None
) -> str:
    """Keep model-written sections out while binding corpus, rules and tokenizer."""
    material = pack.model_dump(mode="json")
    material["chunks"] = [
        chunk.model_dump(mode="json")
        for chunk in pack.chunks
        if chunk.factor
        not in {EnumContextFactor.TRANSCRIPT_EXTRACTS, EnumContextFactor.TAIL_SUMMARY}
    ]
    # pack_id and the full token total include model-written sections too.
    # They must not reintroduce those sections into deterministic identity.
    material.pop("pack_id")
    material["total_token_estimate"] = sum(
        chunk["token_estimate"] for chunk in material["chunks"]
    )
    material["assembly_identity"] = (
        identity.model_dump(mode="json") if identity else None
    )
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
