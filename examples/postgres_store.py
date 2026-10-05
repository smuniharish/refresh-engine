"""A durable state store backed by PostgreSQL, owned by the application.

This module is a reference implementation built on psycopg 3. It applies each
transaction atomically with batched statements. Production deployments typically
share a connection pool (for example `psycopg_pool.AsyncConnectionPool`) instead of
connecting for every call.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from types import MappingProxyType

import psycopg
from psycopg.types.json import Jsonb

from refresh_engine import (
    ResourceFingerprint,
    ResourceState,
    StateStoreABC,
    StateStoreError,
    StateTransactionABC,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS refresh_engine_state (
    resource_id TEXT PRIMARY KEY,
    fingerprint_algorithm TEXT NOT NULL,
    fingerprint_value TEXT NOT NULL,
    version TEXT,
    cheap_indicator TEXT,
    snapshot_metadata JSONB NOT NULL,
    dependencies JSONB NOT NULL,
    refreshed_at TIMESTAMPTZ NOT NULL
)
"""
UPSERT = """
INSERT INTO refresh_engine_state VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (resource_id) DO UPDATE SET
    fingerprint_algorithm = excluded.fingerprint_algorithm,
    fingerprint_value = excluded.fingerprint_value,
    version = excluded.version,
    cheap_indicator = excluded.cheap_indicator,
    snapshot_metadata = excluded.snapshot_metadata,
    dependencies = excluded.dependencies,
    refreshed_at = excluded.refreshed_at
"""


class PostgresStateStore(StateStoreABC):
    """Persist resource state in a PostgreSQL table."""

    def __init__(self, conninfo: str) -> None:
        self.conninfo = conninfo

    async def create_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.conninfo) as connection:
            await connection.execute(SCHEMA)

    async def clear(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.conninfo) as connection:
            await connection.execute("DELETE FROM refresh_engine_state")

    async def load_all(self) -> Mapping[str, ResourceState]:
        try:
            async with await psycopg.AsyncConnection.connect(
                self.conninfo
            ) as connection:
                cursor = await connection.execute("SELECT * FROM refresh_engine_state")
                rows = await cursor.fetchall()
        except psycopg.Error as error:
            raise StateStoreError(f"cannot load state: {error}") from error
        return MappingProxyType(
            {
                row[0]: ResourceState(
                    row[0],
                    ResourceFingerprint(row[1], row[2]),
                    version=row[3],
                    cheap_indicator=row[4],
                    snapshot_metadata=row[5],
                    dependencies=frozenset(row[6]),
                    refreshed_at=row[7],
                )
                for row in rows
            }
        )

    def transaction(self) -> StateTransactionABC:
        return PostgresTransaction(self)


class PostgresTransaction(StateTransactionABC):
    """Apply staged changes in one PostgreSQL transaction."""

    def __init__(self, store: PostgresStateStore) -> None:
        super().__init__()
        self.store = store

    async def apply_changes(
        self,
        puts: Mapping[str, ResourceState],
        deletes: AbstractSet[str],
        /,
    ) -> None:
        rows = [
            (
                state.resource_id,
                state.fingerprint.algorithm,
                state.fingerprint.value,
                state.version,
                state.cheap_indicator,
                Jsonb(dict(state.snapshot_metadata)),
                Jsonb(sorted(state.dependencies)),
                state.refreshed_at,
            )
            for state in puts.values()
        ]
        try:
            async with (
                await psycopg.AsyncConnection.connect(
                    self.store.conninfo
                ) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                await cursor.executemany(
                    "DELETE FROM refresh_engine_state WHERE resource_id = %s",
                    [(resource_id,) for resource_id in sorted(deletes)],
                )
                await cursor.executemany(UPSERT, rows)
        except psycopg.Error as error:
            raise StateStoreError(f"cannot commit state: {error}") from error
