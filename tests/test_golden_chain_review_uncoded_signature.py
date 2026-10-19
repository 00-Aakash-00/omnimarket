# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Uncoded findings become one candidate after three distinct PRs."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from pydantic import ValidationError

from omnimarket.models.model_review_finding_signature import (
    ModelReviewFindingUncoded,
    ModelReviewSignaturePolicy,
    ModelReviewUncodedState,
)
from omnimarket.nodes.node_review_finding_signature_compute.handlers.handler_review_finding_signature import (
    HandlerReviewFindingSignatureCompute,
)
from omnimarket.nodes.node_review_uncoded_signature_reducer.handlers.handler_review_uncoded_signature import (
    HandlerReviewUncodedSignatureReducer,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
NOTES = (
    "Missing timeout for `fetch` calls in src/a.py at line 17.",
    "The call to `load` in lib/b.py misses a timeout at line 99.",
    "Timeouts are missing from calls to `save` in pkg/c.py:42.",
)
COMPUTE = HandlerReviewFindingSignatureCompute()


def finding(
    pr: int, note: str = NOTES[0], **changes: object
) -> ModelReviewFindingUncoded:
    data: dict[str, object] = {
        "correlation_id": uuid4(),
        "finding_id": f"finding-{pr}",
        "review_run_id": f"review-{pr}",
        "repository": "example/service",
        "pr_number": pr,
        "path": "src/a.py",
        "family": "correctness",
        "note": note,
        "voter": "reviewer-a",
        "observed_at": NOW,
    }
    data.update(changes)
    return ModelReviewFindingUncoded.model_validate(data)


def reducer() -> HandlerReviewUncodedSignatureReducer:
    return HandlerReviewUncodedSignatureReducer(states={})


def test_differently_worded_defect_on_three_prs_gives_one_candidate() -> None:
    handler = reducer()
    signed = [COMPUTE.handle(finding(pr, note)) for pr, note in enumerate(NOTES, 1)]
    assert len({item.signature for item in signed}) == 1
    assert handler.handle(signed[0]) is None
    assert handler.handle(signed[1]) is None
    candidate = handler.handle(signed[2])
    assert candidate is not None
    assert candidate.pr_refs == (
        "example/service#1",
        "example/service#2",
        "example/service#3",
    )
    assert len(candidate.examples) == 3
    assert candidate.family == "correctness"
    assert candidate.window_end == NOW
    assert candidate.window_start == NOW - timedelta(days=14)
    assert handler.handle(COMPUTE.handle(finding(4))) is None


def test_two_prs_give_none_even_with_repeated_voters_and_findings() -> None:
    handler = reducer()
    for pr in (1, 2, 1, 2, 1):
        for voter in ("reviewer-a", "reviewer-b"):
            assert handler.handle(COMPUTE.handle(finding(pr, voter=voter))) is None


def test_replayed_events_emit_nothing_new_after_state_roundtrip() -> None:
    states: dict[str, ModelReviewUncodedState] = {}
    handler = HandlerReviewUncodedSignatureReducer(states=states)
    events = [COMPUTE.handle(finding(pr)) for pr in (1, 2, 3)]
    assert sum(handler.handle(event) is not None for event in events) == 1
    restored = {
        key: ModelReviewUncodedState.model_validate_json(value.model_dump_json())
        for key, value in states.items()
    }
    restarted = HandlerReviewUncodedSignatureReducer(states=restored)
    for event in reversed(events):
        assert restarted.handle(event) is None
    assert restored == states


def test_masking_stemming_order_and_family_are_deterministic() -> None:
    first = COMPUTE.handle(
        finding(1, 'Missing timeouts for `fetch` calls in src/a.py:12 with "fast".')
    )
    second = COMPUTE.handle(
        finding(2, "The calls to `load` in lib/b.py miss timeout with 'slow' at 456.")
    )
    assert first.signature == second.signature
    assert first.minhash == second.minhash
    assert len(first.minhash) == 128
    assert first.content_tokens == tuple(sorted(set(first.content_tokens)))
    assert not any(
        value in first.normalized_note for value in ("fetch", "src/a.py", "12", "fast")
    )
    other_family = COMPUTE.handle(finding(1, first.note, family="security"))
    assert other_family.signature != first.signature


def test_unrelated_defects_and_different_families_do_not_group() -> None:
    handler = reducer()
    assert handler.handle(COMPUTE.handle(finding(1))) is None
    assert handler.handle(COMPUTE.handle(finding(2, family="security"))) is None
    assert (
        handler.handle(
            COMPUTE.handle(finding(3, "Credentials leaked through debug logging."))
        )
        is None
    )


def test_minhash_groups_near_duplicates_with_different_exact_signatures() -> None:
    notes = (
        "Missing timeout causes network request worker queue resource exhaustion failure deadlock retries.",
        "Missing timeout causes network request worker queue resource exhaustion failure deadlock retries forever.",
        "Missing timeout causes network request worker queue resource exhaustion failure deadlock retries again.",
    )
    signed = [COMPUTE.handle(finding(pr, note)) for pr, note in enumerate(notes, 1)]
    assert len({item.signature for item in signed}) == 3
    handler = reducer()
    assert handler.handle(signed[0]) is None
    assert handler.handle(signed[1]) is None
    assert handler.handle(signed[2]) is not None


@pytest.mark.parametrize(
    ("age_days", "expected"), [(14, True), (14.001, False), (15, False)]
)
def test_fourteen_day_window_boundary(age_days: float, expected: bool) -> None:
    handler = reducer()
    assert (
        handler.handle(
            COMPUTE.handle(finding(1, observed_at=NOW - timedelta(days=age_days)))
        )
        is None
    )
    assert handler.handle(COMPUTE.handle(finding(2))) is None
    assert (handler.handle(COMPUTE.handle(finding(3))) is not None) is expected


def test_late_old_events_cannot_roll_the_window_back() -> None:
    handler = reducer()
    assert handler.handle(COMPUTE.handle(finding(3))) is None
    for pr in (1, 2):
        assert (
            handler.handle(
                COMPUTE.handle(finding(pr, observed_at=NOW - timedelta(days=15)))
            )
            is None
        )


def test_pr_identity_includes_repository() -> None:
    handler = reducer()
    assert handler.handle(COMPUTE.handle(finding(1))) is None
    assert (
        handler.handle(COMPUTE.handle(finding(1, repository="example/other"))) is None
    )
    assert (
        handler.handle(COMPUTE.handle(finding(1, repository="example/third")))
        is not None
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"defect_code": "T01"},
        {"accepted": False},
        {"observed_at": NOW.replace(tzinfo=None)},
        {"family": "unknown"},
        {"pr_number": 0},
    ],
)
def test_only_typed_accepted_uncoded_findings_enter(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        finding(1, **changes)


def test_empty_content_is_refused_instead_of_one_universal_signature() -> None:
    with pytest.raises(ValueError, match="content tokens"):
        COMPUTE.handle(finding(1, "`name` 123 'value' src/a.py"))


def test_policy_is_loaded_from_contract_and_can_be_injected() -> None:
    handler = reducer()
    root = Path(__file__).parents[1] / "src/omnimarket/nodes"
    contract = yaml.safe_load(
        (root / "node_review_uncoded_signature_reducer/contract.yaml").read_text()
    )
    assert handler.policy.model_dump() == contract["recurrence_policy"]
    custom = HandlerReviewUncodedSignatureReducer(
        states={}, policy=ModelReviewSignaturePolicy(distinct_prs=2)
    )
    assert custom.handle(COMPUTE.handle(finding(1))) is None
    assert custom.handle(COMPUTE.handle(finding(2))) is not None


def test_contracts_wire_the_chain_and_candidate_event() -> None:
    root = Path(__file__).parents[1] / "src/omnimarket/nodes"
    contracts = [
        yaml.safe_load((root / node / "contract.yaml").read_text())
        for node in (
            "node_review_finding_signature_compute",
            "node_review_uncoded_signature_reducer",
        )
    ]
    compute, reduce = contracts
    assert (
        compute["event_bus"]["publish_topics"]
        == reduce["event_bus"]["subscribe_topics"]
    )
    assert reduce["published_events"] == [
        {
            "event_type": "ReviewCodeCandidateProposed",
            "topic": "onex.evt.omnimarket.review-code-candidate-proposed.v1",
        }
    ]
    assert reduce["state_io"]["key"] == "family"
    for contract in contracts:
        ref = contract["handler"]
        assert callable(
            getattr(importlib.import_module(ref["module"]), ref["class"]).handle
        )


def test_default_handler_refuses_to_lose_state_outside_runtime() -> None:
    with pytest.raises(RuntimeError, match="state_io"):
        HandlerReviewUncodedSignatureReducer().handle(COMPUTE.handle(finding(1)))


def test_durable_state_seam_restores_and_flushes_across_handler_instances() -> None:
    from omnibase_infra.runtime.state_io.state_store_adapter import (
        CONTEXTVAR_STATE_IO_ROWS,
    )

    from omnimarket.nodes.node_review_uncoded_signature_reducer.handlers.state_codec import (
        StateIoCodec,
    )

    codec = StateIoCodec()
    persisted: str | None = None
    outputs = []
    events = [COMPUTE.handle(finding(pr)) for pr in (1, 2, 3)]
    for event in events + events:
        token = CONTEXTVAR_STATE_IO_ROWS.set({"correctness": (persisted, 0)})
        try:
            outputs.append(HandlerReviewUncodedSignatureReducer().handle(event))
            persisted = codec.flush("correctness")
            assert persisted is not None
            assert json.loads(persisted)["state"] == "COMPLETED"
        finally:
            CONTEXTVAR_STATE_IO_ROWS.reset(token)
    assert sum(output is not None for output in outputs) == 1


@pytest.mark.asyncio
async def test_lab_bus_chain_uses_runtime_adapters_and_transactional_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real adapters and bus; only the database transport is an in-memory double."""
    from omnibase_core.event_bus.event_bus_inmemory import EventBusInmemory
    from omnibase_core.models.event_bus.model_event_message import ModelEventMessage
    from omnibase_core.models.events.model_event_envelope import ModelEventEnvelope
    from omnibase_infra.runtime.auto_wiring import handler_wiring
    from omnibase_infra.runtime.auto_wiring.models import ModelHandlerRef

    from omnimarket.models.model_review_finding_signature import (
        ModelReviewFindingSignature,
    )
    from tests.runtime_local_compat import LocalRuntimeBusAdapter

    class StateTable:
        def __init__(self) -> None:
            self.rows: dict[str, dict[str, object]] = {}
            self.committed_candidates = 0

        async def load(self, key: str) -> tuple[str, int] | None:
            row = self.rows.get(key)
            if row is None:
                return None
            return str(row["payload_json"]), int(str(row["version"]))

        async def seed(self, key: str, **values: object) -> bool:
            if key in self.rows:
                return False
            self.rows[key] = {**values, "version": 0, "correlation_id": key}
            return True

        async def cas_update(
            self, key: str, expected_version: int, **values: object
        ) -> int:
            row = self.rows[key]
            if row["version"] != expected_version:
                return 0
            self.rows[key] = {**row, **values, "version": expected_version + 1}
            if values.get("pending_emissions"):
                self.committed_candidates += 1
            return 1

        async def select_recoverable_batches(self) -> list[dict[str, object]]:
            return [
                dict(row)
                for row in self.rows.values()
                if row.get("in_flight") and row.get("pending_emissions")
            ]

        async def recover_stale_rows(self) -> list[dict[str, object]]:
            return []

    table = StateTable()
    monkeypatch.setattr(
        handler_wiring, "StateStoreAdapter", lambda *_args, **_kwargs: table
    )
    monkeypatch.setenv(
        "OMNIBASE_INFRA_DB_URL", "postgresql://localhost/review_signature_test"
    )
    root = Path(__file__).parents[1] / "src/omnimarket/nodes"
    compute_contract = yaml.safe_load(
        (root / "node_review_finding_signature_compute/contract.yaml").read_text()
    )
    contract = yaml.safe_load(
        (root / "node_review_uncoded_signature_reducer/contract.yaml").read_text()
    )
    output_map = {
        item["event_type"]: item["topic"] for item in contract["published_events"]
    }
    bus = EventBusInmemory(environment="test", group="review-signature")
    callback = handler_wiring._make_stateful_dispatch_callback(
        HandlerReviewUncodedSignatureReducer(),
        ModelHandlerRef(
            name=contract["input_model"]["name"],
            module=contract["input_model"]["module"],
        ),
        contract["state_io"],
        event_bus=bus,
        output_topic_map=output_map,
    )
    candidates: list[dict[str, object]] = []
    errors: list[bool] = []

    async def on_signed(message: ModelEventMessage) -> None:
        assert message.topic == "onex.evt.omnimarket.review-finding-signed.v1"
        payload = ModelReviewFindingSignature.model_validate_json(message.value)
        await callback(
            ModelEventEnvelope(payload=payload, correlation_id=payload.correlation_id)
        )

    async def on_candidate(message: ModelEventMessage) -> None:
        candidates.append(json.loads(message.value))

    adapter = LocalRuntimeBusAdapter(
        handler=COMPUTE,
        handler_name=compute_contract["name"],
        input_model_cls=ModelReviewFindingUncoded,
        output_topic=compute_contract["terminal_event"],
        bus=bus,
        on_error=lambda: errors.append(True),
    )
    await bus.start()
    try:
        await bus.subscribe(
            compute_contract["event_bus"]["subscribe_topics"][0],
            on_message=adapter.on_message,
            group_id="compute",
        )
        await bus.subscribe(
            contract["event_bus"]["subscribe_topics"][0],
            on_message=on_signed,
            group_id="reduce",
        )
        await bus.subscribe(
            contract["terminal_event"], on_message=on_candidate, group_id="proposals"
        )
        events = [finding(pr, note) for pr, note in enumerate(NOTES, 1)]
        for event in events + events:
            await bus.publish(
                compute_contract["event_bus"]["subscribe_topics"][0],
                key=None,
                value=event.model_dump_json().encode(),
            )
        assert errors == []
        assert len(candidates) == 1
        assert table.committed_candidates == 1
        assert not table.rows["correctness"]["in_flight"]
        assert not table.rows["correctness"]["pending_emissions"]
    finally:
        await bus.close()


def test_proposal_mark_survives_window_expiry() -> None:
    handler = reducer()
    assert (
        sum(handler.handle(COMPUTE.handle(finding(pr))) is not None for pr in (1, 2, 3))
        == 1
    )
    for pr in (4, 5, 6):
        assert (
            handler.handle(
                COMPUTE.handle(finding(pr, observed_at=NOW + timedelta(days=30)))
            )
            is None
        )


@pytest.mark.parametrize(
    ("matching_components", "expected"), [(103, True), (102, False)]
)
def test_minhash_threshold_is_at_least_point_eight(
    matching_components: int, expected: bool
) -> None:
    events = [
        COMPUTE.handle(finding(pr, note))
        for pr, note in enumerate(
            (
                "Missing timeout for network calls.",
                "Missing timeout for network requests.",
                "Missing timeout for network fetches.",
            ),
            1,
        )
    ]
    first = events[0].model_copy(update={"minhash": (1,) * 128})
    near = tuple([1] * matching_components + [2] * (128 - matching_components))
    handler = reducer()
    assert handler.handle(first) is None
    assert handler.handle(events[1].model_copy(update={"minhash": near})) is None
    assert (
        handler.handle(events[2].model_copy(update={"minhash": near})) is not None
    ) is expected
