# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Whole exemplar admission and authority fidelity against real builder output."""

from __future__ import annotations

import asyncio
from itertools import permutations
from pathlib import Path
from typing import cast

import pytest
import yaml
from omnibase_core.event_bus.event_bus_inmemory import EventBusInmemory
from omnibase_core.models.event_bus.model_event_message import ModelEventMessage
from omnibase_core.models.events.model_event_envelope import ModelEventEnvelope
from omnibase_infra.runtime.auto_wiring.handler_wiring import (
    ProtocolHandleable,
    _make_dispatch_callback,
)
from pydantic import ValidationError

from omnimarket.nodes.node_context_pack_builder_compute.handlers.handler_context_pack_builder import (
    HandlerContextPackBuilder,
)
from omnimarket.pack.model_context_pack_builder_request import (
    ModelContextPackBuilderRequest,
)
from omnimarket.pack.model_context_pack_builder_result import (
    ModelContextPackBuilderResult,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("binding", ["typed-runtime", "in-memory-bus"])
@pytest.mark.parametrize("budget", [100, 21, 10])
def test_same_item_admission_and_fidelity_across_bindings(
    binding: str, budget: int
) -> None:
    node = (
        Path(__file__).resolve().parents[3]
        / "src/omnimarket/nodes/node_context_pack_builder_compute"
    )
    contract = yaml.safe_load((node / "contract.yaml").read_text())
    command_topic = contract["runtime_dispatch"]["command_topic"]
    terminal = contract["terminal_event"]
    req = request(budget=budget)
    expected = HandlerContextPackBuilder().handle(req)
    callback = _make_dispatch_callback(
        cast("ProtocolHandleable", HandlerContextPackBuilder())
    )
    envelope = ModelEventEnvelope[object](
        payload=req.model_dump(mode="json"), event_type=command_topic
    )

    async def exchange() -> ModelContextPackBuilderResult:
        if binding == "typed-runtime":
            dispatched = await callback(envelope)
            assert dispatched is not None
            return ModelContextPackBuilderResult.model_validate(
                dispatched.output_events[0]
            )
        bus = EventBusInmemory()
        await bus.start()
        answers: list[ModelContextPackBuilderResult] = []

        async def receive(message: ModelEventMessage) -> None:
            assert message.value is not None
            answers.append(
                ModelContextPackBuilderResult.model_validate_json(message.value)
            )

        async def dispatch(message: ModelEventMessage) -> None:
            assert message.value is not None
            wire = ModelEventEnvelope[object].model_validate_json(message.value)
            dispatched = await callback(wire)
            assert dispatched is not None
            result = ModelContextPackBuilderResult.model_validate(
                dispatched.output_events[0]
            )
            await bus.publish(
                terminal, key=None, value=result.model_dump_json().encode()
            )

        await bus.subscribe(terminal, group_id="builder-result", on_message=receive)
        await bus.subscribe(
            command_topic, group_id="builder-command", on_message=dispatch
        )
        try:
            await bus.publish(
                command_topic, key=None, value=envelope.model_dump_json().encode()
            )
            assert len(answers) == 1
            return answers[0]
        finally:
            await bus.shutdown()

    assert asyncio.run(exchange()) == expected


def test_contract_and_overlay_declare_caller_owned_item_policy() -> None:
    node = (
        Path(__file__).resolve().parents[3]
        / "src/omnimarket/nodes/node_context_pack_builder_compute"
    )
    contract = yaml.safe_load((node / "contract.yaml").read_text())
    overlay = yaml.safe_load((node / "overlay.yaml").read_text())
    assert contract["inputs"]["assembly_identity"]["required"] is False
    assert (
        contract["assembly_policy"]["item_level_profile_field"]
        == "item_level_exemplars"
    )
    assert overlay["profile"]["item_level_exemplars"] is False
    payload = request().model_dump(mode="json")
    payload["profile"].update(overlay["profile"])
    configured = ModelContextPackBuilderRequest.model_validate(payload)
    assert not configured.profile.item_level_exemplars
    assert HandlerContextPackBuilder().handle(configured).status.value == "ok"


def request(*, budget: int = 100) -> ModelContextPackBuilderRequest:
    return ModelContextPackBuilderRequest.model_validate(
        {
            "contract_hash": "contract",
            "generated_at": "2026-10-27T11:00:00Z",
            "profile": {
                "model_id": "fixture",
                "required_factors": ["golden_chain"],
                "optional_factors": ["exemplar"],
                "factor_precedence": ["golden_chain", "exemplar"],
                "token_budget": budget,
                "item_level_exemplars": True,
                "token_estimation_method": "caller_exact",
                "tokenizer_source": "fixture-tokenizer",
            },
            "assembly_identity": {
                "corpus_version": "corpus-1",
                "ruleset_hash": "rules-1",
                "tokenizer_id": "tokenizer-1",
            },
            "artifacts": [
                artifact("authority", "golden_chain", 20),
                artifact("a", "exemplar", 90, score=1.0),
                artifact("b", "exemplar", 50, score=0.9),
                artifact("c", "exemplar", 40, score=0.8),
                artifact("d", "exemplar", 30, score=0.7),
            ],
        }
    )


def artifact(
    ident: str, factor: str, tokens: int, *, score: float | None = None
) -> dict[str, object]:
    return {
        "factor": factor,
        "content": f"Whole original authority or exemplar content for item {ident}.",
        "token_estimate": tokens,
        "provenance": "generated",
        "source_artifact_hash": ident,
        "source_contract_hash": "contract",
        "effectiveness_score": score,
        "confidence": 0.9,
    }


def test_item_admission_skips_large_items_fills_in_score_order_and_counts_omitted() -> (
    None
):
    built = HandlerContextPackBuilder().handle(request())
    assert built.status.value == "ok", built.errors
    assert built.context_pack is not None
    assert [c.source_artifact_hash for c in built.context_pack.chunks] == [
        "authority",
        "b",
        "d",
    ]
    assert built.context_pack.total_token_estimate == 100
    assert built.omitted_exemplar_count == 2
    assert [e.source_artifact_hash for e in built.excluded_by_budget] == ["c", "a"]
    assert all(
        c.token_estimation_method == "caller_exact" for c in built.context_pack.chunks
    )
    assert built.fidelity is not None
    assert built.fidelity.passed


def test_item_admission_never_builds_header_only_when_no_exemplar_fits() -> None:
    built = HandlerContextPackBuilder().handle(request(budget=21))
    assert built.status.value == "failed"
    assert built.failure_reason == "FIRST_EXEMPLAR_EXCEEDS_BUDGET"
    assert built.omitted_exemplar_count == 4
    assert built.context_pack is None


def test_item_admission_reserves_required_before_any_exemplar() -> None:
    built = HandlerContextPackBuilder().handle(request(budget=10))
    assert built.failure_class is not None
    assert built.failure_class.value == "token_budget_exceeded"
    assert "authority" in " ".join(built.errors)
    assert built.context_pack is None


def test_item_admission_restores_precedence_by_id_after_score_selection() -> None:
    req = request()
    # Source order is independent of admission score order; restore by identity,
    # never by the content (two different inputs may contain identical words).
    changed = tuple(
        item.model_copy(
            update={
                "source_priority": {"b": 2, "d": 1}.get(item.source_artifact_hash, 0)
            }
        )
        for item in req.artifacts
    )
    result = HandlerContextPackBuilder().handle(
        req.model_copy(update={"artifacts": changed})
    )
    assert result.context_pack is not None
    assert [c.source_artifact_hash for c in result.context_pack.chunks] == [
        "authority",
        "d",
        "b",
    ]


def test_item_admission_is_permutation_invariant() -> None:
    req = request()
    expected = HandlerContextPackBuilder().handle(req)
    for items in permutations(req.artifacts):
        assert (
            HandlerContextPackBuilder().handle(
                req.model_copy(update={"artifacts": items})
            )
            == expected
        )


def test_item_admission_uses_score_before_source_id() -> None:
    req = request()
    items = tuple(
        a.model_copy(update={"effectiveness_score": 0.1})
        if a.source_artifact_hash == "b"
        else a
        for a in req.artifacts
    )
    built = HandlerContextPackBuilder().handle(
        req.model_copy(update={"artifacts": items})
    )
    assert built.context_pack is not None
    assert [c.source_artifact_hash for c in built.context_pack.chunks] == [
        "authority",
        "c",
        "d",
    ]


def test_item_admission_breaks_score_ties_by_confidence() -> None:
    req = request(budget=80)
    confidence = {"b": 0.1, "c": 0.9, "d": 0.8}
    items = tuple(
        a.model_copy(
            update={
                "effectiveness_score": 0.5,
                "confidence": confidence[a.source_artifact_hash],
            }
        )
        if a.source_artifact_hash in confidence
        else a
        for a in req.artifacts
    )
    built = HandlerContextPackBuilder().handle(
        req.model_copy(update={"artifacts": items})
    )
    assert built.context_pack is not None
    assert [c.source_artifact_hash for c in built.context_pack.chunks] == [
        "authority",
        "c",
    ]


def test_item_exclusions_restore_reverse_precedence_after_score_admission() -> None:
    req = request()
    priority = {"a": 2, "b": 3, "c": 1, "d": 4}
    items = tuple(
        a.model_copy(update={"source_priority": priority[a.source_artifact_hash]})
        if a.source_artifact_hash in priority
        else a
        for a in req.artifacts
    )
    built = HandlerContextPackBuilder().handle(
        req.model_copy(update={"artifacts": items})
    )
    assert [e.source_artifact_hash for e in built.excluded_by_budget] == ["a", "c"]


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_item_mode_refuses_unranked_or_nonfinite_exemplars(bad: float | None) -> None:
    payload = request().model_dump(mode="json")
    payload["artifacts"][1]["effectiveness_score"] = bad
    with pytest.raises(ValidationError):
        ModelContextPackBuilderRequest.model_validate(payload)


def test_item_mode_requires_explicit_assembly_identity() -> None:
    payload = request().model_dump(mode="json")
    payload.pop("assembly_identity")
    with pytest.raises(ValidationError, match="assembly_identity"):
        ModelContextPackBuilderRequest.model_validate(payload)


def test_builder_rejects_authority_corrupted_during_packing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnibase_core.models.pack.model_context_chunk import ModelContextChunk

    from omnimarket.nodes.node_context_pack_builder_compute.handlers import (
        handler_context_pack_builder as module,
    )
    from omnimarket.pack.model_context_pack_artifact import ModelContextPackArtifact
    from omnimarket.pack.model_context_profile import ModelContextProfile

    original = module._chunks

    def corrupt(
        artifacts: tuple[ModelContextPackArtifact, ...], profile: ModelContextProfile
    ) -> tuple[ModelContextChunk, ...]:
        chunks = original(artifacts, profile)
        return (
            chunks[0].model_copy(update={"content": chunks[0].content[:26]}),
            *chunks[1:],
        )

    monkeypatch.setattr(module, "_chunks", corrupt)
    built = HandlerContextPackBuilder().handle(request())
    assert built.status.value == "failed"
    assert built.failure_reason == "AUTHORITY_FIDELITY_FAILED"
    assert built.context_pack is None
    assert built.fidelity is not None
    assert not built.fidelity.passed


def test_assembly_digest_excludes_model_written_content_and_derived_pack_id() -> None:
    from omnibase_core.enums.enum_context_factor import EnumContextFactor

    from omnimarket.handlers.handler_context_pack_assembly import (
        compute_context_assembly_hash,
    )

    req = request()
    built = HandlerContextPackBuilder().handle(req)
    assert built.context_pack is not None
    pack = built.context_pack
    summary = pack.chunks[-1].model_copy(
        update={
            "factor": EnumContextFactor.TAIL_SUMMARY,
            "content": "model output",
            "token_estimate": 3,
        }
    )
    with_summary = pack.model_copy(
        update={
            "chunks": (*pack.chunks, summary),
            "pack_id": "model-dependent-pack-id",
            "total_token_estimate": pack.total_token_estimate + 3,
        }
    )
    assert compute_context_assembly_hash(
        pack, req.assembly_identity
    ) == compute_context_assembly_hash(with_summary, req.assembly_identity)


@pytest.mark.parametrize("field", ["corpus_version", "ruleset_hash", "tokenizer_id"])
def test_each_assembly_identity_component_changes_full_digest(field: str) -> None:
    req = request()
    built = HandlerContextPackBuilder().handle(req)
    payload = req.model_dump(mode="json")
    payload["assembly_identity"][field] += "-changed"
    changed = HandlerContextPackBuilder().handle(
        ModelContextPackBuilderRequest.model_validate(payload)
    )
    assert built.assembly_hash is not None
    assert len(built.assembly_hash) == 64
    assert changed.assembly_hash != built.assembly_hash
    assert changed.context_pack == built.context_pack


def test_fidelity_checker_rejects_truncated_or_missing_authority_from_real_output() -> (
    None
):
    from omnimarket.handlers.handler_context_pack_fidelity import (
        check_authority_fidelity,
    )

    req = request()
    built = HandlerContextPackBuilder().handle(req)
    assert built.context_pack is not None
    pack = built.context_pack
    assert check_authority_fidelity(
        req.artifacts, pack, req.profile.required_factors
    ).passed
    authority, *optional = pack.chunks
    for corrupted in (
        authority.model_copy(update={"content": authority.content[:26]}),
        authority.model_copy(
            update={"content": authority.content.replace("authority", "invented")}
        ),
    ):
        tampered = pack.model_copy(update={"chunks": (corrupted, *optional)})
        verdict = check_authority_fidelity(
            req.artifacts, tampered, req.profile.required_factors
        )
        assert not verdict.passed
        assert "authority" in " ".join(verdict.violations)
    missing = pack.model_copy(update={"chunks": tuple(optional)})
    assert not check_authority_fidelity(
        req.artifacts, missing, req.profile.required_factors
    ).passed
