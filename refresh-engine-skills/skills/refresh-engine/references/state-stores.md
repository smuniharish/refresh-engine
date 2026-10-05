# State stores

## What is stored

A `ResourceState` records, for each resource, the outcome of its last successful
refresh: `fingerprint` (algorithm and value), `version`, `cheap_indicator`,
`snapshot_metadata`, `dependencies`, and `refreshed_at`. At the end of a refresh,
the engine commits one transaction that:

- writes the state of every resource whose operation succeeded;
- removes the state of every resource whose deletion succeeded;
- writes a retry marker for unchanged resources whose planned refresh did not
  complete;
- updates the version, cheap indicator, and metadata of unchanged resources when
  they drift, keeping their fingerprint and refresh time.

Nothing is written for failed resources, nothing at all under `FAIL_FAST` when
anything failed, and no transaction is opened when there is nothing to write.

## Choosing a store

- `InMemoryStateStore` (default): for tests, notebooks, and processes that run
  continuously and can afford a full refresh after a restart, because state is lost
  when the process exits.
- A durable store: for every other production use. Without one, every restart
  refreshes every resource.

## Writing a durable store

Subclass `StateStoreABC` and `StateTransactionABC`. The transaction base class
stages `put()` and `delete()` calls and passes them to `apply_changes()`, which
must apply them atomically:

```python
from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from refresh_engine import (
    ResourceState,
    StateStoreABC,
    StateStoreError,
    StateTransactionABC,
)


class PostgresStateStore(StateStoreABC):
    def __init__(self, pool: ConnectionPool, namespace: str) -> None:
        self.pool = pool
        self.namespace = namespace

    async def load_all(self) -> Mapping[str, ResourceState]:
        rows = await self.pool.fetch_states(self.namespace)
        return {row.resource_id: to_state(row) for row in rows}

    def transaction(self) -> StateTransactionABC:
        return PostgresTransaction(self)


class PostgresTransaction(StateTransactionABC):
    def __init__(self, store: PostgresStateStore) -> None:
        super().__init__()
        self.store = store

    async def apply_changes(
        self,
        puts: Mapping[str, ResourceState],
        deletes: AbstractSet[str],
        /,
    ) -> None:
        try:
            async with self.store.pool.transaction() as connection:
                await connection.delete_states(self.store.namespace, deletes)
                await connection.upsert_states(self.store.namespace, puts.values())
        except DatabaseError as error:
            raise StateStoreError(f"cannot commit state: {error}") from error
```

Requirements:

- **Atomicity.** Apply all changes or none of them. `puts` and `deletes` never
  share an ID.
- **Exact round-trips.** Return every field from `load_all()` exactly as it was
  written, with the same types. A store that changes values, for example turning
  tuples in metadata into lists, makes unchanged resources look drifted, so they
  are rewritten on every refresh.
- **Failures raise.** Raise `StateStoreError` or any exception. The engine rolls
  back and reports a `persistence` issue with status `FAILED`.
- **No blocking.** Use an async driver, or wrap a synchronous one with
  `asyncio.to_thread`.
- **Call `super().__init__()`** in a transaction subclass that defines
  `__init__`.

A suitable table has one row per resource:

```sql
CREATE TABLE resource_state (
    namespace             TEXT NOT NULL,
    resource_id           TEXT NOT NULL,
    fingerprint_algorithm TEXT NOT NULL,
    fingerprint_value     TEXT NOT NULL,
    version               TEXT,
    cheap_indicator       TEXT,
    snapshot_metadata     JSONB NOT NULL,
    dependencies          JSONB NOT NULL,
    refreshed_at          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (namespace, resource_id)
);
```

Store `snapshot_metadata` as JSON only if metadata values are JSON types, and
convert `dependencies` to and from a `frozenset`. Complete SQLite and PostgreSQL
stores are in the repository's
[examples](https://github.com/smuniharish/refresh-engine/tree/HEAD/examples):
`sqlite_store.py` and `postgres_store.py`.

## Concurrency

Run one engine at a time per namespace of stored state. Two engines that share a
namespace do not corrupt it, because commits are atomic, but they duplicate work and
the last commit wins. For several processes, partition resources into namespaces,
or elect one refresher.
