"""Real SQLite failure cases, written before T5 implementation.

Catch reordered/lost choices, both clients overwriting one version, replacement
of a missing row, cross-tenant conflicts and revoke-before-register revival.
All writes use a private pytest store; no shared database or real credential.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from omnimarket.nodes.node_projection_tenant_credentials.handlers.handler_tenant_credentials_store import (
    apply_credential_registered,
    apply_credential_revoked,
)
from omnimarket.projection.sqlite_database import SqliteDatabaseAdapter

TABLE = "delegation_routing_tenant_overlay"


def event(
    models: list[str], tenant: str = "tenant-a", **extra: object
) -> dict[str, object]:
    return {
        "tenant_id": tenant,
        "provider": "openrouter",
        "name": "test-key",
        "api_key_ref": f"cred_{tenant}_fake",
        "models": models,
        "metadata": {"model": models[0]},
        **extra,
    }


def stored(db: SqliteDatabaseAdapter, tenant: str = "tenant-a") -> dict[str, object]:
    return db.query(TABLE, {"tenant_id": tenant})[0]


def test_create_keeps_customer_order_and_starts_at_version_one(tmp_path: Path) -> None:
    db = SqliteDatabaseAdapter(tmp_path / "credentials.sqlite")
    apply_credential_registered(event(["vendor/b", "vendor/a"]), db)
    row = stored(db)
    assert row.get("models") == ["vendor/b", "vendor/a"]
    assert row.get("version") == 1
    assert row["model_name"] == "vendor/b"


def test_two_clients_with_one_version_apply_exactly_one_whole_list(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = SqliteDatabaseAdapter(tmp_path / "credentials.sqlite")
    apply_credential_registered(event(["vendor/a", "vendor/b"]), db)
    apply_credential_registered(event(["vendor/b", "vendor/a"], expected_version=1), db)
    apply_credential_registered(event(["vendor/c"], expected_version=1), db)
    row = stored(db)
    assert row.get("models") == ["vendor/b", "vendor/a"]
    assert row.get("version") == 2
    assert row["model_name"] == "vendor/b"
    assert "credential_models_version_conflict" in caplog.text
    assert "current_version=2" in caplog.text


def test_concurrent_connections_cannot_both_replace_one_version(tmp_path: Path) -> None:
    """A sequential check alone would miss a read-then-write version race."""
    path = tmp_path / "credentials.sqlite"
    db = SqliteDatabaseAdapter(path)
    apply_credential_registered(event(["vendor/a"]), db)
    ready = Barrier(2)
    choices = [["vendor/b", "vendor/c"], ["vendor/d", "vendor/e"]]

    def replace(models: list[str]) -> None:
        ready.wait()
        apply_credential_registered(
            event(models, expected_version=1), SqliteDatabaseAdapter(path)
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(replace, choices))
    row = stored(db)
    assert row.get("version") == 2
    assert row.get("models") in choices
    assert row["model_name"] == row["models"][0]


def test_replace_of_a_missing_row_is_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db = SqliteDatabaseAdapter(tmp_path / "credentials.sqlite")
    apply_credential_registered(event(["vendor/a"], expected_version=1), db)
    assert db.query(TABLE, {"tenant_id": "tenant-a"}) == []
    assert "credential_models_version_conflict" in caplog.text


def test_versions_are_scoped_to_each_tenant_and_legacy_rotation_still_works(
    tmp_path: Path,
) -> None:
    db = SqliteDatabaseAdapter(tmp_path / "credentials.sqlite")
    apply_credential_registered(event(["vendor/a"]), db)
    apply_credential_registered(event(["vendor/b"], tenant="tenant-b"), db)
    apply_credential_registered(event(["vendor/c"], expected_version=1), db)
    assert stored(db).get("version") == 2
    assert stored(db, "tenant-b").get("models") == ["vendor/b"]
    legacy = event(["vendor/d"])
    legacy.pop("models")
    apply_credential_registered(legacy, db)
    assert stored(db).get("models") == ["vendor/d"]
    assert stored(db).get("version") == 3


def test_revocation_wins_before_or_after_registration(tmp_path: Path) -> None:
    db = SqliteDatabaseAdapter(tmp_path / "credentials.sqlite")
    data = event(["vendor/a", "vendor/b"])
    apply_credential_registered(data, db)
    apply_credential_revoked(data, db)
    apply_credential_registered(event(["vendor/c"], expected_version=1), db)
    assert stored(db)["secret_ref"] is None
    assert stored(db).get("models") == ["vendor/a", "vendor/b"]
    other = event(["vendor/x"], tenant="tenant-b")
    apply_credential_revoked(other, db)
    apply_credential_registered(other, db)
    assert db.query(TABLE, {"tenant_id": "tenant-b"}) == []
