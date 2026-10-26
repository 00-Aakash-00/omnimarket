# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""Atomic whole-list credential route replacement at the database boundary."""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType

from omnibase_core.models.projection.model_upsert_plan import build_upsert_plan

from omnimarket.models.model_credential_route_write import ModelCredentialRouteWrite


class HandlerCredentialRouteWrite:
    """Bind one guarded SQL statement for the existing projection writer."""

    @staticmethod
    def prepare(
        row: dict[str, object], expected_version: int | None
    ) -> ModelCredentialRouteWrite:
        """Own the complete route SQL; adapters only execute this bound plan."""
        sqlite_write, parameters = HandlerCredentialRouteWrite.statement(
            row, expected_version, postgres=False
        )
        postgres_write, _ = HandlerCredentialRouteWrite.statement(
            row, expected_version, postgres=True
        )
        return ModelCredentialRouteWrite(
            row=MappingProxyType(dict(row)),
            expected_version=expected_version,
            parameters=MappingProxyType(parameters),
            sqlite_write=sqlite_write,
            postgres_write=postgres_write,
            sqlite_read=(
                "SELECT version FROM delegation_routing_tenant_overlay "
                "WHERE tenant_id = :tenant_id AND task_type = :task_type"
            ),
            postgres_read=(
                "SELECT version FROM delegation_routing_tenant_overlay "
                "WHERE tenant_id = %(tenant_id)s AND task_type = %(task_type)s"
            ),
            sqlite_models_write=(
                # An empty choice is an unknown one: NULL, as a revoke-first
                # delivery order leaves it.
                "UPDATE tenant_inference_credentials SET models = NULLIF(:models, '[]') "
                "WHERE tenant_id = :tenant_id AND api_key_ref = :secret_ref "
                "AND revoked_at IS NULL RETURNING api_key_ref"
                if row.get("secret_ref") is not None
                else None
            ),
            # The same choice on Postgres, in the route write's transaction.
            # The version rises as the asynchronous writer's does, so a state
            # row read back afterwards never undoes this write.
            postgres_models_write=(
                "UPDATE tenant_inference_credentials "
                "SET models = NULLIF(%(models)s::JSONB, '[]'::JSONB), "
                "version = version + 1 "
                "WHERE tenant_id = %(tenant_id)s AND api_key_ref = %(secret_ref)s "
                "AND revoked_at IS NULL RETURNING api_key_ref"
                if row.get("secret_ref") is not None
                else None
            ),
        )

    @staticmethod
    def statement(
        row: dict[str, object],
        expected_version: int | None,
        *,
        postgres: bool,
    ) -> tuple[str, dict[str, object]]:
        """One guarded statement; expected-version writes never insert missing rows.

        Identifier validation is delegated to the canonical upsert-plan builder.
        The credential tombstone guard also applies to the conflict-update arm.
        """
        table = "delegation_routing_tenant_overlay"
        build_upsert_plan(table=table, conflict_key="tenant_id,task_type", row=row)
        bind: Callable[[str], str] = (
            (lambda key: f"%({key})s") if postgres else (lambda key: f":{key}")
        )
        params = dict(row)
        guard = (
            "NOT EXISTS (SELECT 1 FROM tenant_inference_credentials "
            f"WHERE api_key_ref = {bind('secret_ref')} AND revoked_at IS NOT NULL)"
        )
        fields = [
            key for key in row if key not in {"tenant_id", "task_type", "created_at"}
        ]
        if expected_version is not None:
            params["expected_version"] = expected_version
            updates = ", ".join(f"{key} = {bind(key)}" for key in fields)
            statement = (
                f"UPDATE {table} SET {updates}, version = version + 1 "
                f"WHERE tenant_id = {bind('tenant_id')} AND task_type = {bind('task_type')} "
                f"AND version = {bind('expected_version')} AND {guard} RETURNING version"
            )
        else:
            keys = list(row)
            updates = ", ".join(f"{key} = EXCLUDED.{key}" for key in fields)
            statement = (
                f"INSERT INTO {table} ({', '.join(keys)}, version) "
                f"SELECT {', '.join(bind(key) for key in keys)}, 1 WHERE {guard} "
                f"ON CONFLICT (tenant_id, task_type) DO UPDATE SET {updates}, "
                f"version = {table}.version + 1 WHERE {guard} RETURNING version"
            )
        return statement, params
