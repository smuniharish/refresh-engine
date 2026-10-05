# Testing integrations

## In-memory fakes

Test sources and operations against small in-memory fakes. Keep the production
operation's logic in a class or function that accepts a fake destination.

```python
import pytest

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshRequest,
    RefreshStatus,
    Resource,
    ResourceSnapshot,
)


class FakeSource:
    def __init__(self, contents: dict[str, str]) -> None:
        self.contents = contents

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult([Resource(key) for key in sorted(self.contents)])

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource.resource_id, content=self.contents[resource.resource_id]
        )


class FakeDestination:
    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.broken: set[str] = set()

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        if resource.resource_id in self.broken:
            raise ConnectionError("unavailable")
        if action is PlanAction.DELETE:
            self.rows.pop(resource.resource_id, None)
        elif snapshot is not None:
            self.rows[resource.resource_id] = snapshot.content


@pytest.mark.asyncio
async def test_failed_write_is_retried_by_next_refresh() -> None:
    source = FakeSource({"a": "1", "b": "1"})
    destination = FakeDestination()
    async with RefreshEngine(
        source, destination, config=RefreshConfig(max_concurrency=1)
    ) as engine:
        await engine.refresh()

        source.contents["b"] = "2"
        destination.broken.add("b")
        failed = await engine.refresh()
        assert failed.status is RefreshStatus.PARTIAL
        assert failed.errors[0].resource_id == "b"
        assert destination.rows["b"] == "1"

        destination.broken.clear()
        retried = await engine.refresh()
        assert retried.status is RefreshStatus.SUCCESS
        assert destination.rows == {"a": "1", "b": "2"}
```

The `asyncio` marker requires pytest-asyncio; with `asyncio_mode = "auto"`, it can
be omitted.

## What to assert

| Assert | To verify |
| --- | --- |
| `status` | The overall outcome. |
| `added_count`, `modified_count`, `dependency_changed_count`, `unchanged_count`, `deleted_count` | Change detection, including the fingerprint strategy and cheap indicators. |
| `refreshed_count`, `skipped_count`, `impacted_count` | Which resources received operations. |
| `errors` (`phase`, `resource_id`, `exception_type`) | Failures and where they happened. |
| `executions` (`attempts`, `succeeded`) | Retries and skipped dependents. |
| `warnings` | Partial discoveries, unknown targets, and empty selectors. |

Cover at least: a first refresh, an unchanged second refresh with
`refreshed_count == 0`, a modification, a deletion with a complete discovery, no
deletion with `complete=False`, a failing operation that is retried by the next
refresh, and, when dependencies exist, a dependency-aware refresh.

## Events

```python
from refresh_engine import EventKind, EventPublisher, RefreshEngine, RefreshEvent

events = EventPublisher()
received: list[RefreshEvent] = []
events.subscribe(received.append)

async with RefreshEngine(source, operation, events=events) as engine:
    await engine.refresh()

kinds = [event.kind for event in received]
assert kinds[0] is EventKind.REFRESH_STARTED
assert kinds[-1] is EventKind.REFRESH_COMPLETED
```

Leaving the `async with` block drains pending events. Without it, call
`await events.drain()` before asserting.

## Tips

- Use `max_concurrency=1` when assertions depend on the order of operations.
- Share one `InMemoryStateStore` between two engines to simulate a restart.
- Coordinate concurrency with `asyncio.Event` objects, not sleeps.
- Use `InMemoryMetrics` to assert on metrics.
- Test that the cheap indicator changes whenever fingerprinted data changes.
- To test a durable store, commit through `store.transaction()` and compare
  `load_all()` with what was written, field by field.
