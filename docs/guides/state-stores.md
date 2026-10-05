# State stores

The state store remembers, for every resource, the outcome of its last successful
refresh. It is what makes refreshes incremental and restarts safe.

## What is stored, and when

Each [`ResourceState`][refresh_engine.ResourceState] holds a resource's
fingerprint, version, cheap indicator, snapshot metadata, dependencies, and the time
of its last successful refresh. At the end of a refresh, the engine commits one
transaction that:

- writes the new state of every resource whose operation succeeded;
- removes the state of every resource whose deletion succeeded;
- writes a retry marker for every unchanged resource whose planned refresh did not
  complete, so that the next refresh retries it;
- updates the version, cheap indicator, and metadata of unchanged resources when
  they drift, without changing their fingerprint or refresh time.

Nothing is written for resources that failed, and nothing at all under the
`FAIL_FAST` policy when anything failed. When there is nothing to write, no
transaction is opened.

## The default store

[`InMemoryStateStore`][refresh_engine.InMemoryStateStore] keeps state in process
memory. Loading returns an immutable snapshot in constant time, and commits replace
the stored mapping atomically. State is lost when the process exits, so the first
refresh after a restart treats every resource as added.

## Durable stores

To keep state across restarts, implement
[`StateStore`][refresh_engine.StateStore] on top of your database. The simplest way
is to subclass [`StateStoreABC`][refresh_engine.StateStoreABC] and
[`StateTransactionABC`][refresh_engine.StateTransactionABC]. The transaction base
class stages `put` and `delete` calls in memory and hands them to a single method,
`apply_changes`, which you implement atomically:

```python
from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from refresh_engine import ResourceState, StateStoreABC, StateTransactionABC


class DatabaseStore(StateStoreABC):
    def __init__(self, database: Database) -> None:
        self.database = database

    async def load_all(self) -> Mapping[str, ResourceState]:
        return {state.resource_id: state for state in await self.database.states()}

    def transaction(self) -> StateTransactionABC:
        return DatabaseTransaction(self.database)


class DatabaseTransaction(StateTransactionABC):
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database

    async def apply_changes(
        self,
        puts: Mapping[str, ResourceState],
        deletes: AbstractSet[str],
        /,
    ) -> None:
        async with self.database.transaction():
            await self.database.delete_states(deletes)
            await self.database.upsert_states(puts.values())
```

Rules for a correct store:

- **Atomic commits.** Apply all staged changes or none of them.
- **Raise on failure.** Raise [`StateStoreError`][refresh_engine.StateStoreError] or
  any exception; the engine rolls the transaction back and reports the refresh as
  failed.
- **Round-trip every field.** Store the fingerprint's algorithm and value, version,
  cheap indicator, snapshot metadata, dependencies, and refresh time, and return
  them unchanged from `load_all()`.
- **Keep the event loop free.** Use an asynchronous driver, or run blocking calls
  with `asyncio.to_thread`.

The repository includes two complete reference stores, which you can copy and adapt:

??? example "SQLite reference store"

    ```python
    --8<--
    examples/sqlite_store.py
    --8<--
    ```

??? example "PostgreSQL reference store"

    ```python
    --8<--
    examples/postgres_store.py
    --8<--
    ```

## One writer per namespace

Run one engine at a time against the same stored states. Two engines that share a
store do not corrupt it, because every commit is atomic, but they repeat each
other's work and the last commit wins. For several processes, either partition the
resources so that each process owns a namespace (for example, a separate table or
key prefix), or elect a single refresher.

## Example

```python
--8<--
examples/10_sqlite_state_store.py
--8<--
```

```text
--8<--
examples/output/10_sqlite_state_store.txt
--8<--
```
