"""Consumer-owned PostgreSQL implementation of the public StateStore ABC."""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType

import psycopg

from refresh_engine import (
    ResourceFingerprint,
    ResourceState,
    StateStoreABC,
    StateStoreError,
    StateTransactionABC,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS refresh_engine_resource_state (
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


class PostgresStateTransaction(StateTransactionABC):
    def __init__(self, store: PostgresStateStore) -> None:
        self._store = store
        self._puts: dict[str, ResourceState] = {}
        self._deletes: set[str] = set()
        self._closed = False

    async def put(self, state: ResourceState) -> None:
        self._ensure_open()
        self._puts[state.resource_id] = state
        self._deletes.discard(state.resource_id)

    async def delete(self, resource_id: str) -> None:
        self._ensure_open()
        self._deletes.add(resource_id)
        self._puts.pop(resource_id, None)

    async def commit(self) -> None:
        self._ensure_open()
        await self._store.commit(tuple(self._puts.values()), tuple(self._deletes))
        self._closed = True

    async def rollback(self) -> None:
        self._puts.clear()
        self._deletes.clear()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise StateStoreError("state transaction is already closed")


class PostgresStateStore(StateStoreABC):
    def __init__(self, connection_string: str) -> None:
        self._connection_string = connection_string

    async def initialize(self) -> None:
        async with await psycopg.AsyncConnection.connect(
            self._connection_string
        ) as connection:
            await connection.execute(_SCHEMA)

    async def load_all(self) -> Mapping[str, ResourceState]:
        async with await psycopg.AsyncConnection.connect(
            self._connection_string
        ) as connection:
            cursor = await connection.execute("""
                SELECT resource_id, fingerprint_algorithm, fingerprint_value,
                       version, cheap_indicator, snapshot_metadata, dependencies,
                       refreshed_at
                FROM refresh_engine_resource_state
                ORDER BY resource_id
                """)
            rows = await cursor.fetchall()
        return MappingProxyType(
            {
                row[0]: ResourceState(
                    resource_id=row[0],
                    fingerprint=ResourceFingerprint(row[1], row[2]),
                    version=row[3],
                    cheap_indicator=row[4],
                    snapshot_metadata=row[5],
                    dependencies=frozenset(row[6]),
                    refreshed_at=row[7],
                )
                for row in rows
            }
        )

    def transaction(self) -> PostgresStateTransaction:
        return PostgresStateTransaction(self)

    async def commit(
        self,
        puts: tuple[ResourceState, ...],
        deletes: tuple[str, ...],
    ) -> None:
        async with await psycopg.AsyncConnection.connect(
            self._connection_string
        ) as connection:
            for resource_id in deletes:
                await connection.execute(
                    """
                    DELETE FROM refresh_engine_resource_state
                    WHERE resource_id = %s
                    """,
                    (resource_id,),
                )
            for state in puts:
                await connection.execute(
                    """
                    INSERT INTO refresh_engine_resource_state(
                        resource_id, fingerprint_algorithm, fingerprint_value,
                        version, cheap_indicator, snapshot_metadata, dependencies,
                        refreshed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                    ON CONFLICT(resource_id) DO UPDATE SET
                        fingerprint_algorithm = excluded.fingerprint_algorithm,
                        fingerprint_value = excluded.fingerprint_value,
                        version = excluded.version,
                        cheap_indicator = excluded.cheap_indicator,
                        snapshot_metadata = excluded.snapshot_metadata,
                        dependencies = excluded.dependencies,
                        refreshed_at = excluded.refreshed_at
                    """,
                    (
                        state.resource_id,
                        state.fingerprint.algorithm,
                        state.fingerprint.value,
                        state.version,
                        state.cheap_indicator,
                        json.dumps(dict(state.snapshot_metadata)),
                        json.dumps(sorted(state.dependencies)),
                        state.refreshed_at,
                    ),
                )
