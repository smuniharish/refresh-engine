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
actually committed — see [integration.md](integration.md).

## Failure handling and safety

Source: `src/refresh_engine/api/engine.py`,
`src/refresh_engine/detection/detector.py`,
`docs/guides/state-and-failure-safety.md`.

### Discovery failure is an error, not "zero resources"

If `ResourceSource.discover()` (or consuming its `DiscoveryResult.resources`
iterator) raises, the engine wraps it as `DiscoveryError` and the whole
refresh fails (`RefreshStatus.FAILED`) with that error in
`RefreshResult.errors`. It never falls through to "discovered zero
resources," which would look identical to mass deletion. A duplicate
resource ID within one discovery also raises `DiscoveryError`.

### Partial/incomplete discovery never implies deletion

`DiscoveryResult(complete=False)` is a first-class signal, not an error. When
`complete=False`:

- Resources that *are* yielded are processed normally (can still be `ADDED`/
  `MODIFIED`/`UNCHANGED`).
- Resource IDs present in the previous state but absent from this discovery
  are **not** inferred as `DELETED` — `ChangeDetector.detect()` explicitly
  skips deletion inference and appends a diagnostic instead ("deletion
  inference suppressed because discovery was incomplete").
- Only a `complete=True` discovery may plan `PlanAction.DELETE` for IDs that
  disappeared.

[`examples/06_resilience_and_partial_discovery.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/06_resilience_and_partial_discovery.py)
demonstrates this: after marking the source `complete=False` and removing a
resource from it, that resource's prior state is retained and nothing is
deleted.

### State never advances on failure

A resource's `ResourceState` is only staged for commit if its
`ResourceExecutionResult.succeeded` is `True`. Failed executions keep the
resource's previous stored state, so the *next* refresh will still see it as
changed (not silently marked "handled").

### `RefreshStatus` semantics

| Status | Meaning |
| --- | --- |
| `SUCCESS` | No errors occurred. |
| `PARTIAL` | Some resources failed but `FailurePolicy` allowed the refresh to continue and produce a result. |
| `FAILED` | The refresh could not produce any usable result (`FAIL_FAST` with a failure, no executions at all, or an exception during the lifecycle itself, e.g. `DiscoveryError`). |
| `CANCELLED` | Propagated `asyncio.CancelledError`; the caller's own `await engine.refresh()` raises `CancelledError` rather than returning a result. |
| `SKIPPED` | `OverlapPolicy.SKIP_IF_RUNNING` rejected the request while another was in flight; `RefreshResult.skipped(reason)`. |

### Cancellation is cooperative and rolls back

```python
refresh_task = asyncio.create_task(engine.refresh())
...
refresh_task.cancel()
try:
    await refresh_task
except asyncio.CancelledError:
    ...
```

Cancellation propagates through discovery, snapshotting, and execution
(workers are explicitly cancelled and awaited with `return_exceptions=True`
so nothing is orphaned). No state transaction is committed for a cancelled
refresh — see
[`examples/11_cancellation_and_shutdown.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/11_cancellation_and_shutdown.py)
for a mid-operation cancellation followed by a store check that shows zero
committed states.

### Operations must be idempotent

Because timeouts and retries can cause an operation to run more than once (or
be cancelled after the external side effect already completed but before
acknowledgement), `RefreshOperation` implementations must be safe to run more
than once for the same input, and must propagate `CancelledError` rather than
swallowing it. Use application-level idempotency keys where the destination
system supports them.

### Shutdown ordering (avoid orphaned work)

```python
try:
    await scheduler.start()
    await shutdown_requested.wait()
finally:
    await scheduler.stop()          # AsyncScheduler.cancel_active_on_stop controls its own work
    await engine.close()            # RefreshConfig.cancel_active_on_shutdown controls coordinated work
```

`RefreshEngine.close()` awaits the coordinator's shutdown (optionally
cancelling the active/queued work per `cancel_active_on_shutdown`) and drains
the event publisher. Always call `close()` once you are done with an engine,
even if you never started a scheduler.
