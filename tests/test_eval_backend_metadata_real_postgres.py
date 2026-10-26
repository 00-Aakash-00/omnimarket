# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Exercise the real eval writer in a rollback-only tenant transaction.

Uses the already migrated eval tables only; never creates schemas, roles or
other live tables. The lab command supplies the restricted writer DSN.
"""

import json
import os
from uuid import uuid4

import asyncpg
import pytest

from omnimarket.events.delegation_eval import ModelDelegationEvalRunRequest
from omnimarket.events.delegation_gate_eval.model_gate_eval_item import (
    ModelGateEvalItem,
)
from omnimarket.models.delegation_eval_backend import ModelDelegationEvalBackend
from omnimarket.nodes.node_delegation_eval_run_orchestrator.handlers import (
    HandlerDelegationEvalRun,
)
from omnimarket.nodes.node_projection_delegation_eval.handlers.handler_delegation_eval_writer import (
    DelegationEvalProjectionWriter,
)
from omnimarket.projection.runner import MessageMeta


@pytest.mark.integration
async def test_backend_pin_and_latency_survive_real_writer_and_readback() -> None:
    dsn = os.environ.get("INTEGRATION_POSTGRES_DSN")
    if not dsn:
        pytest.skip("INTEGRATION_POSTGRES_DSN is required for the real writer proof")
    tenant = os.environ.get("ONEX_TENANT_ID", "11111111-1111-1111-1111-111111111111")
    connection = await asyncpg.connect(dsn)
    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, true)", tenant)

        class Source:
            def get_labelled_items(self, *args):
                return (
                    ModelGateEvalItem(
                        item_id="rollback-only",
                        task_class="reasoning",
                        stratum="all",
                        label="unlabelable",
                        prompt_text="Hello",
                        recorded_answer="Hello",
                    ),
                )

        backend = ModelDelegationEvalBackend(
            backend_id="rollback-only",
            endpoint="http://example.invalid:8130",
            host="test",
            model="model",
            model_file="test.gguf",
            quant="Q8_0",
        )
        request = ModelDelegationEvalRunRequest(
            tenant_id=tenant,
            manifest_id=str(uuid4()),
            rater_role="unlabelled:test",
            rubric_version="v1",
            gate_version="test",
            backend_configuration=backend,
            generation_sha256="a" * 64,
            item_latencies_ms={"rollback-only": 1.25},
        )
        payload = HandlerDelegationEvalRun(Source()).handle(request).payload

        class BoundTransaction:
            async def connect(self):
                pass

            async def close(self):
                pass

            async def execute(self, sql, *params, tenant=None):
                return list(await connection.fetch(sql, *params))

        writer = DelegationEvalProjectionWriter()
        writer._db = BoundTransaction()
        receipt = await writer._project_one_message(
            payload.model_dump(mode="json"),
            MessageMeta(
                topic=writer.subscribe_topics[1], partition=0, offset=0, fallback_id=""
            ),
        )
        assert receipt["rows_upserted"] > 1
        row = await connection.fetchrow(
            "SELECT backend_configuration, latency_ms FROM public.delegation_eval_item_verdicts WHERE tenant_id=$1 AND eval_run_id=$2",
            request.tenant_id,
            payload.eval_run_id,
        )
        assert json.loads(row["backend_configuration"]) == backend.model_dump()
        assert row["latency_ms"] == 1.25
        results = await connection.fetch(
            "SELECT backend_configuration FROM public.delegation_eval_results WHERE tenant_id=$1 AND eval_run_id=$2",
            request.tenant_id,
            payload.eval_run_id,
        )
        assert results
        assert all(
            json.loads(r["backend_configuration"]) == backend.model_dump()
            for r in results
        )
        duplicate = await writer._project_one_message(
            payload.model_dump(mode="json"),
            MessageMeta(
                topic=writer.subscribe_topics[1], partition=0, offset=0, fallback_id=""
            ),
        )
        assert duplicate["rows_upserted"] == 0
    finally:
        await transaction.rollback()
        await connection.close()
