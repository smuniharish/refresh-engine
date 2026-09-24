"""Consumer-owned SQLite implementation of the public StateStore ABC."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from refresh_engine.api.abc import StateStoreABC, StateTransactionABC
from refresh_engine.core.models import ResourceFingerprint, ResourceState
from refresh_engine.errors import StateStoreError

_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS refresh_engine_schema (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_state (
    resource_id TEXT PRIMARY KEY,
    fingerprint_algorithm TEXT NOT NULL,
    fingerprint_value TEXT NOT NULL,
    version TEXT,
    cheap_indicator TEXT,
    snapshot_metadata TEXT NOT NULL,
    dependencies TEXT NOT NULL,
    refreshed_at TEXT NOT NULL
);
"""


def _encode_json(value: Any, field: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise StateStoreError(f"{field} is not JSON serializable") from error


class SQLiteStateTransaction(StateTransactionABC):
    """Stage state changes and commit them in one SQLite transaction."""

    def __init__(self, store: SQLiteStateStore) -> None:
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
        puts = tuple(self._serialize(state) for state in self._puts.values())
        deletes = tuple(self._deletes)
        try:
            await asyncio.to_thread(self._store._commit, puts, deletes)
        except StateStoreError:
            raise
        except Exception as error:
            raise StateStoreError("failed to commit SQLite state") from error
        self._closed = True

    async def rollback(self) -> None:
        self._puts.clear()
        self._deletes.clear()
        self._closed = True

    async def __aenter__(self) -> SQLiteStateTransaction:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        if exc_type is not None and not self._closed:
            await self.rollback()
        elif not self._closed:
            await self.commit()

    def _ensure_open(self) -> None:
        if self._closed:
            raise StateStoreError("state transaction is already closed")

    @staticmethod
    def _serialize(state: ResourceState) -> tuple[object, ...]:
        return (
            state.resource_id,
            state.fingerprint.algorithm,
            state.fingerprint.value,
            state.version,
            state.cheap_indicator,
            _encode_json(dict(state.snapshot_metadata), "snapshot_metadata"),
            _encode_json(sorted(state.dependencies), "dependencies"),
            state.refreshed_at.isoformat(),
        )


class SQLiteStateStore(StateStoreABC):
    """Process-safe durable state store backed by a SQLite database file.

    SQLite calls execute in worker threads so async refresh paths remain non-blocking.
    Each operation opens a short-lived connection, while a process-local lock protects
    schema initialization and write transactions.
    """

    def __init__(self, path: str | Path, *, busy_timeout: float = 5.0) -> None:
        if busy_timeout <= 0:
            raise ValueError("busy_timeout must be positive")
        self.path = Path(path)
        if str(self.path) == ":memory:":
            raise ValueError("use InMemoryStateStore for non-durable in-memory state")
        self._busy_timeout = busy_timeout
        self._write_lock = threading.RLock()
        self._initialized = False

    async def load_all(self) -> Mapping[str, ResourceState]:
        try:
            states = await asyncio.to_thread(self._load_all)
        except StateStoreError:
            raise
        except Exception as error:
            raise StateStoreError("failed to load SQLite state") from error
        return MappingProxyType(states)

    def transaction(self) -> SQLiteStateTransaction:
        return SQLiteStateTransaction(self)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=self._busy_timeout,
            isolation_level=None,
        )
        try:
            connection.execute(
                f"PRAGMA busy_timeout = {int(self._busy_timeout * 1000)}"
            )
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        if self._initialized:
            return
        with self._write_lock:
            if self._initialized:
                return
            connection.executescript(_SCHEMA)
            row = connection.execute(
                "SELECT version FROM refresh_engine_schema WHERE singleton = 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO refresh_engine_schema(singleton, version)
                    VALUES (1, ?)
                    """,
                    (_SCHEMA_VERSION,),
                )
            elif row[0] != _SCHEMA_VERSION:
                raise StateStoreError(
                    f"unsupported SQLite state schema version: {row[0]}"
                )
            self._initialized = True

    def _load_all(self) -> dict[str, ResourceState]:
        connection = self._connect()
        try:
            self._ensure_schema(connection)
            rows = connection.execute("""
                SELECT resource_id, fingerprint_algorithm, fingerprint_value,
                       version, cheap_indicator, snapshot_metadata, dependencies,
                       refreshed_at
                FROM resource_state
                ORDER BY resource_id
                """).fetchall()
        finally:
            connection.close()
        try:
            return {
                row[0]: ResourceState(
                    resource_id=row[0],
                    fingerprint=ResourceFingerprint(row[1], row[2]),
                    version=row[3],
                    cheap_indicator=row[4],
                    snapshot_metadata=json.loads(row[5]),
                    dependencies=frozenset(json.loads(row[6])),
                    refreshed_at=datetime.fromisoformat(row[7]),
                )
                for row in rows
            }
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise StateStoreError(
                "SQLite state contains invalid serialized data"
            ) from error

    def _commit(
        self,
        puts: tuple[tuple[object, ...], ...],
        deletes: tuple[str, ...],
    ) -> None:
        with self._write_lock:
            connection = self._connect()
            try:
                self._ensure_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.executemany(
                        "DELETE FROM resource_state WHERE resource_id = ?",
                        ((resource_id,) for resource_id in deletes),
                    )
                    connection.executemany(
                        """
                        INSERT INTO resource_state(
                            resource_id, fingerprint_algorithm, fingerprint_value,
                            version, cheap_indicator, snapshot_metadata, dependencies,
                            refreshed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(resource_id) DO UPDATE SET
                            fingerprint_algorithm = excluded.fingerprint_algorithm,
                            fingerprint_value = excluded.fingerprint_value,
                            version = excluded.version,
                            cheap_indicator = excluded.cheap_indicator,
                            snapshot_metadata = excluded.snapshot_metadata,
                            dependencies = excluded.dependencies,
                            refreshed_at = excluded.refreshed_at
                        """,
                        puts,
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            finally:
                connection.close()
