# State management

Source: `src/refresh_engine/stores/memory.py`, `src/refresh_engine/api/abc.py`,
`docs/guides/state-and-failure-safety.md`.

## What state the engine keeps

`ResourceState(resource_id, fingerprint, version=None, cheap_indicator=None,
snapshot_metadata={}, dependencies=frozenset(), refreshed_at=<utc timestamp>)`
is the **last successfully processed** state for a resource — not the full
snapshot/content. The engine never persists `ResourceSnapshot.content`; only
the fingerprint and small metadata needed for the next comparison.

## Default: `InMemoryStateStore`

`RefreshEngine(source, operation)` (no `store=`) uses `InMemoryStateStore()` —
copy-on-write dict guarded by a single `asyncio.Lock`, useful for tests and
process-local workloads. **It does not survive process restart.** For anything
that must survive a restart, inject a durable adapter.

## `StateStore` / `StateTransaction` contract

```python
from refresh_engine import StateStore, StateStoreABC, StateTransaction, StateTransactionABC

class MyStore(StateStoreABC):
    async def load_all(self) -> Mapping[str, ResourceState]: ...
    def transaction(self) -> StateTransactionABC: ...

class MyTransaction(StateTransactionABC):
    async def put(self, state: ResourceState) -> None: ...
    async def delete(self, resource_id: str) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    # __aenter__/__aexit__ default: commit on success, rollback on exception
```

Either inherit the ABCs, or implement the equivalent methods structurally to
satisfy the `StateStore`/`StateTransaction` protocols — both work with
`RefreshEngine(store=...)`.

`RefreshEngine._persist` opens exactly **one transaction per refresh** and, for
every successfully executed resource, calls `transaction.put(state)` (for
`PlanAction.REFRESH`) or `transaction.delete(resource_id)` (for
`PlanAction.DELETE`); unchanged resources are re-`put` with their existing
state to refresh `refreshed_at` bookkeeping consistency. **The transaction is
never opened at all if `FailurePolicy.FAIL_FAST` and any resource failed** —
no partial state is persisted in that case.

## The critical invariant: never advance state before success

> The engine advances a resource's successful state only after its operation
> succeeds. Discovery failure is not interpreted as an empty source.
> (`docs/guides/state-and-failure-safety.md`, verified against
> `RefreshEngine._persist` and `_execute_lifecycle`.)

If you write a custom `StateStore`, **do not** add your own eager writes
outside the transaction boundary the engine gives you — that would defeat this
guarantee. Durable adapters must commit staged changes atomically (a single
transaction/batch write), not row-by-row as `put()`/`delete()` are called.

## A wrapper store that adds behavior without touching commit semantics

```python
from refresh_engine import InMemoryStateStore, RefreshEngine

class AuditedStore:
    def __init__(self) -> None:
        self.backend = InMemoryStateStore()
        self.load_count = 0

    async def load_all(self):
        self.load_count += 1
        return await self.backend.load_all()

    def transaction(self):
        return self.backend.transaction()

engine = RefreshEngine(source, operation, store=AuditedStore())
```

This is a **structural** `StateStore` (no ABC inheritance needed) that
delegates transaction semantics entirely to another store.

## Durable adapters (SQLite / PostgreSQL) are consumer-owned reference code

`examples/sqlite_store.py` and `examples/postgres_store.py` (used by
`examples/12_sqlite_state_store.py` and `examples/14_postgres_state_store.py`)
demonstrate atomic commits, restart continuity, and rollback against the
public `StateStoreABC` boundary. **These adapters are not part of the
production `refresh_engine` package** — database drivers and schema/migration
choices are deliberately left to the application (see
`docs/architecture/decisions.md`, "Application-managed durable adapters").
Copy and adapt the pattern; do not expect `refresh_engine` to import
`sqlite3`/`psycopg` for you.

Keep blocking database calls off the event loop in a durable adapter (e.g.
`asyncio.to_thread` for a synchronous DB-API driver, or a native async driver
like `psycopg` in async mode).

## Testing state persistence

Assert on `await engine.store.load_all()` (or your injected store's
equivalent) rather than only on `RefreshResult` counts, to verify state was
actually committed — see [testing.md](testing.md).
