# Testing integrations

refresh-engine makes integrations easy to test: everything runs in memory, results
report exactly what happened, and events describe every step.

## Use in-memory fakes

Replace the real source and destination with small in-memory classes. Keep the
production operation's logic in a function you can call with a fake destination.

<!-- test -->
```python
import asyncio

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
        return DiscoveryResult(Resource(key) for key in sorted(self.contents))

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
        if snapshot is None:
            self.rows.pop(resource.resource_id, None)
        else:
            self.rows[resource.resource_id] = snapshot.content


async def test_a_failed_write_is_retried_by_the_next_refresh() -> None:
    source = FakeSource({"a": "1", "b": "1"})
    destination = FakeDestination()
    config = RefreshConfig(max_concurrency=1)
    async with RefreshEngine(source, destination, config=config) as engine:
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
        assert retried.modified_count == 1
        assert destination.rows == {"a": "1", "b": "2"}


asyncio.run(test_a_failed_write_is_retried_by_the_next_refresh())
```

With pytest and [pytest-asyncio](https://pytest-asyncio.readthedocs.io), write the
test as an `async def` function and drop the `asyncio.run` call.

## Assert on results

[`RefreshResult`][refresh_engine.RefreshResult] counts are the most precise
assertions:

| Assert | To verify |
| --- | --- |
| `added_count`, `modified_count`, `deleted_count`, `unchanged_count` | Change detection, including your fingerprint strategy. |
| `refreshed_count`, `skipped_count` | Which resources received an operation. |
| `errors`, `executions` | Failures, their phases, and retry attempts. |
| `warnings` | Partial discovery and unknown targeted IDs. |
| `status` | The overall outcome. |

## Assert on events

Subscribe a recording handler to check the exact lifecycle, or the attributes your
logs and dashboards rely on:

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

## Tips

- Set `max_concurrency=1` when a test depends on the order of operations.
- Share one [`InMemoryStateStore`][refresh_engine.InMemoryStateStore] between two
  engines to simulate a restart.
- Coordinate concurrency tests with `asyncio.Event` objects instead of sleeps.
- Test the reliability of cheap indicators: a change to anything your fingerprint
  covers must also change the indicator.
