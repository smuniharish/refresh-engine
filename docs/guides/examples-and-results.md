# Examples

All examples use the public `refresh_engine` API. Copy the shared source and operation
into a Python file, then add any scenario that follows.

## Shared source and operation

A source discovers lightweight identities and snapshots each selected resource. The
operation owns the application side effect.

```python
from collections.abc import AsyncIterator, Mapping

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


class DictionarySource:
    def __init__(
        self,
        values: Mapping[str, object],
        dependencies: Mapping[str, set[str]] | None = None,
    ) -> None:
        self.values = dict(values)
        self.dependencies = dependencies or {}
        self.complete = True

    async def discover(self) -> DiscoveryResult:
        async def resources() -> AsyncIterator[Resource]:
            for resource_id in sorted(self.values):
                yield Resource(
                    resource_id,
                    dependencies=frozenset(
                        self.dependencies.get(resource_id, set())
                    ),
                )

        return DiscoveryResult(resources(), complete=self.complete)

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource.resource_id,
            content=self.values[resource.resource_id],
            dependencies=resource.dependencies,
        )


class Recorder:
    def __init__(self) -> None:
        self.refreshed: list[str] = []

    async def __call__(
        self,
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        assert resource is not None
        self.refreshed.append(resource.resource_id)
```

`complete=False` is a safety signal: omitted resource IDs are not treated as deleted.

## Incremental, full, and targeted refresh

```python
import asyncio

from refresh_engine import RefreshEngine, RefreshMode


async def modes() -> None:
    source = DictionarySource({"alpha": 1, "beta": 2})
    operation = Recorder()
    engine = RefreshEngine(source, operation)

    first = await engine.refresh()
    unchanged = await engine.refresh()

    source.values["alpha"] = 3
    incremental = await engine.refresh()
    full = await engine.refresh(mode=RefreshMode.FULL)
    targeted = await engine.refresh(
        mode=RefreshMode.TARGETED,
        resource_ids={"beta"},
    )

    print(f"first: refreshed={first.refreshed_count}")
    print(
        f"unchanged: unchanged={unchanged.unchanged_count}, "
        f"refreshed={unchanged.refreshed_count}"
    )
    print(
        f"incremental: modified={incremental.modified_count}, "
        f"refreshed={incremental.refreshed_count}"
    )
    print(f"full: refreshed={full.refreshed_count}")
    print(f"targeted: refreshed={targeted.refreshed_count}")
    await engine.close()


asyncio.run(modes())
```

Output:

```text
first: refreshed=2
unchanged: unchanged=2, refreshed=0
incremental: modified=1, refreshed=1
full: refreshed=2
targeted: refreshed=1
```

## Dependency-aware refresh

Declare dependencies as “this resource depends on these resource IDs.” The planner
refreshes dependencies before their transitive dependents.

```python
import asyncio

from refresh_engine import ContentHash, RefreshEngine, RefreshMode


async def dependencies() -> None:
    source = DictionarySource(
        {"base": {"version": 1}, "derived": {"version": 1}},
        dependencies={"derived": {"base"}},
    )
    operation = Recorder()
    engine = RefreshEngine(source, operation, fingerprint=ContentHash())
    await engine.refresh()

    operation.refreshed.clear()
    source.values["base"] = {"version": 2}
    result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)

    print(
        f"dependency-aware: modified={result.modified_count}, "
        f"impacted={result.impacted_count}, "
        f"refreshed={operation.refreshed}"
    )
    await engine.close()


asyncio.run(dependencies())
```

Output:

```text
dependency-aware: modified=1, impacted=1, refreshed=['base', 'derived']
```

## Async scheduling

The scheduler owns interval timing; the engine still owns overlap handling and refresh
state.

```python
import asyncio

from refresh_engine import AsyncScheduler, RefreshEngine, TriggerSource


async def scheduling() -> None:
    engine = RefreshEngine(DictionarySource({"alpha": 1}), Recorder())
    completed = asyncio.Event()

    async def scheduled_refresh():
        result = await engine.refresh(trigger=TriggerSource.SCHEDULED)
        completed.set()
        return result

    scheduler = AsyncScheduler(
        scheduled_refresh,
        interval=60,
        run_immediately=True,
    )
    await scheduler.start()
    await asyncio.wait_for(completed.wait(), timeout=1)
    await scheduler.stop()
    await engine.close()
    print("scheduled refresh: completed=True")


asyncio.run(scheduling())
```

Output:

```text
scheduled refresh: completed=True
```

## Retry and partial-discovery safety

```python
import asyncio

from refresh_engine import FailurePolicy, RefreshConfig, RefreshEngine, RetryConfig


async def resilience() -> None:
    source = DictionarySource({"alpha": 1, "beta": 2})
    attempts: dict[str, int] = {}

    async def flaky(resource, snapshot, action, request) -> None:
        resource_id = resource.resource_id
        attempts[resource_id] = attempts.get(resource_id, 0) + 1
        if resource_id == "alpha" and attempts[resource_id] == 1:
            raise OSError("transient")

    engine = RefreshEngine(
        source,
        flaky,
        config=RefreshConfig(
            failure_policy=FailurePolicy.RETRY_THEN_CONTINUE,
            retry=RetryConfig(max_attempts=2, initial_delay=0),
        ),
    )
    result = await engine.refresh()

    source.complete = False
    del source.values["beta"]
    partial = await engine.refresh()

    print(
        f"retry: status={result.status.value}, attempts={attempts}; "
        f"partial discovery deletions={partial.deleted_count}"
    )
    await engine.close()


asyncio.run(resilience())
```

Output:

```text
retry: status=success, attempts={'alpha': 2, 'beta': 1}; partial discovery deletions=0
```

## Persistent store injection

The engine defaults to `InMemoryStateStore`. Any object with `load_all()` and
`transaction()` satisfies the structural `StateStore` protocol. The following wrapper
shows the injection point without inheriting an engine class:

```python
import asyncio

from refresh_engine import InMemoryStateStore, RefreshEngine


class AuditedStore:
    def __init__(self) -> None:
        self.backend = InMemoryStateStore()
        self.loads = 0

    async def load_all(self):
        self.loads += 1
        return await self.backend.load_all()

    def transaction(self):
        return self.backend.transaction()


async def custom_store() -> None:
    store = AuditedStore()
    engine = RefreshEngine(
        DictionarySource({"configuration": {"enabled": True}}),
        Recorder(),
        store=store,
    )
    result = await engine.refresh()
    print(f"custom store: status={result.status.value}, loads={store.loads}")
    await engine.close()


asyncio.run(custom_store())
```

Output:

```text
custom store: status=success, loads=1
```

A durable implementation uses the same methods but delegates to its database and
implements atomic commit/rollback in its transaction object. The application owns the
driver, credentials, pooling, schema migrations, and adapter lifecycle.

## Additional outcomes

Additional integration scenarios produce the following outcomes on Python 3.12:

| Scenario | Observed result |
| --- | --- |
| event and query triggers | `query: refreshed=1, tag_matches=True` |
| async and external scheduling | `external refresh: status=success` |
| lifecycle events and structlog | `status=success, event_kinds=13, handler_errors=0` |
| coalescing with a custom store | `first=success, second=success, store_loads=1` |
| filesystem source | `first_refreshed=1, second_refreshed=0` |
| 10,000 unchanged resources | `unchanged=10000, refreshed=0` |
| generic application resource kinds | `resource_kinds=5, refreshed=5` |
| cancellation and rollback | `propagated=True, committed_states=0` |
| SQLite restart continuity | `unchanged=1, refreshed=0` |
| ABC-based extensions | `refresh configuration` |
| PostgreSQL continuity and rollback | `postgres state store: continuity=True, rollback=True` |

The PostgreSQL scenario runs against PostgreSQL 16. Refresh IDs, timestamps, and
durations vary between runs and are omitted.
