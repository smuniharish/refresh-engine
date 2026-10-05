"""A durable state store backed by SQLite, owned by the application.

This module is a reference implementation. It persists the state of every
resource in one table and applies each transaction atomically. SQLite calls run in
worker threads so that refreshes never block the event loop.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from refresh_engine import (
    ResourceFingerprint,
    ResourceState,
    StateStoreABC,
    StateStoreError,
    StateTransactionABC,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS resource_state (
    resource_id TEXT PRIMARY KEY,
    fingerprint_algorithm TEXT NOT NULL,
    fingerprint_value TEXT NOT NULL,
    version TEXT,
    cheap_indicator TEXT,
    snapshot_metadata TEXT NOT NULL,
    dependencies TEXT NOT NULL,
    refreshed_at TEXT NOT NULL
)
"""
UPSERT = """
INSERT INTO resource_state VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(resource_id) DO UPDATE SET
    fingerprint_algorithm = excluded.fingerprint_algorithm,
    fingerprint_value = excluded.fingerprint_value,
    version = excluded.version,
    cheap_indicator = excluded.cheap_indicator,
    snapshot_metadata = excluded.snapshot_metadata,
    dependencies = excluded.dependencies,
    refreshed_at = excluded.refreshed_at
"""

type Row = tuple[str, str, str, str | None, str | None, str, str, str]


def to_row(state: ResourceState) -> Row:
    return (
        state.resource_id,
        state.fingerprint.algorithm,
        state.fingerprint.value,
        state.version,
        state.cheap_indicator,
        json.dumps(dict(state.snapshot_metadata), sort_keys=True),
        json.dumps(sorted(state.dependencies)),
        state.refreshed_at.isoformat(),
    )


def from_row(row: Row) -> ResourceState:
    return ResourceState(
        row[0],
        ResourceFingerprint(row[1], row[2]),
        version=row[3],
        cheap_indicator=row[4],
        snapshot_metadata=json.loads(row[5]),
        dependencies=frozenset(json.loads(row[6])),
        refreshed_at=datetime.fromisoformat(row[7]),
    )


class SQLiteStateStore(StateStoreABC):
    """Persist resource state in a SQLite database file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, autocommit=False)
        connection.execute(SCHEMA)
        return connection

    async def load_all(self) -> Mapping[str, ResourceState]:
        try:
            return await asyncio.to_thread(self._load_all)
        except (sqlite3.Error, ValueError) as error:
            raise StateStoreError(f"cannot load state: {error}") from error

    def _load_all(self) -> Mapping[str, ResourceState]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM resource_state").fetchall()
        connection.close()
        states = {row[0]: from_row(row) for row in rows}
        return MappingProxyType(states)

    def transaction(self) -> StateTransactionABC:
        return SQLiteTransaction(self)


class SQLiteTransaction(StateTransactionABC):
    """Apply staged changes in one SQLite transaction."""

    def __init__(self, store: SQLiteStateStore) -> None:
        super().__init__()
        self.store = store

    async def apply_changes(
        self,
        puts: Mapping[str, ResourceState],
        deletes: AbstractSet[str],
        /,
    ) -> None:
        rows = [to_row(state) for state in puts.values()]
        try:
            await asyncio.to_thread(self._apply, rows, sorted(deletes))
        except sqlite3.Error as error:
            raise StateStoreError(f"cannot commit state: {error}") from error

    def _apply(self, rows: list[Row], deletes: list[str]) -> None:
        connection = self.store.connect()
        try:
            with connection:
                connection.executemany(
                    "DELETE FROM resource_state WHERE resource_id = ?",
                    [(resource_id,) for resource_id in deletes],
                )
                connection.executemany(UPSERT, rows)
        finally:
            connection.close()
