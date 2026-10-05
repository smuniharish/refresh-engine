# Getting started

## Install

refresh-engine requires Python 3.12 or newer.

=== "uv"

    ```console
    uv add refresh-engine
    ```

=== "pip"

    ```console
    python -m pip install refresh-engine
    ```

Its only runtime dependencies are [structlog](https://www.structlog.org) and
[tenacity](https://tenacity.readthedocs.io).

## Integrate in three steps

An integration consists of a **source** that lists and reads resources, and an
**operation** that applies each change to its destination.

### 1. Describe the source

A source implements two coroutines. `discover()` lists the resources cheaply and
reports whether the listing is complete. `snapshot()` reads one resource in full.

```python
from refresh_engine import DiscoveryResult, Resource, ResourceSnapshot

CATALOG = {"apples": 120, "pears": 80}


class InventorySource:
    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(Resource(item) for item in sorted(CATALOG))

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource.resource_id, content=CATALOG[resource.resource_id]
        )
```

### 2. Describe the operation

The operation receives the resource, its snapshot, the planned action, and the
request. It raises an exception to report a failure.

```python
from refresh_engine import PlanAction, RefreshRequest, Resource, ResourceSnapshot

SEARCH_INDEX: dict[str, int] = {}


async def update_index(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    if action is PlanAction.DELETE:
        SEARCH_INDEX.pop(resource.resource_id, None)
    elif snapshot is not None:
        SEARCH_INDEX[resource.resource_id] = snapshot.content
```

### 3. Run refreshes

<!-- test -->
```python
import asyncio

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)

CATALOG = {"apples": 120, "pears": 80}
SEARCH_INDEX: dict[str, int] = {}


class InventorySource:
    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(Resource(item) for item in sorted(CATALOG))

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource.resource_id, content=CATALOG[resource.resource_id]
        )


async def update_index(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    if action is PlanAction.DELETE:
        SEARCH_INDEX.pop(resource.resource_id, None)
    elif snapshot is not None:
        SEARCH_INDEX[resource.resource_id] = snapshot.content


async def main() -> None:
    async with RefreshEngine(InventorySource(), update_index) as engine:
        first = await engine.refresh()
        print(f"{first.status}: refreshed {first.refreshed_count}", SEARCH_INDEX)

        CATALOG["pears"] = 75
        del CATALOG["apples"]
        second = await engine.refresh()
        print(
            f"{second.status}: modified {second.modified_count},"
            f" deleted {second.deleted_count}",
            SEARCH_INDEX,
        )


asyncio.run(main())
```

Output:

```text
success: refreshed 2 {'apples': 120, 'pears': 80}
success: modified 1, deleted 1 {'pears': 75}
```

The first refresh records a fingerprint for every resource. The second one
refreshes only the modified resource and deletes the removed one. `async with`
closes the engine, which waits for running work and delivers pending events.

## Next steps

- Learn the vocabulary in [Concepts](concepts.md).
- Choose a [refresh mode](guides/refresh-modes.md), make change detection cheap
  with [cheap indicators](guides/change-detection.md#cheap-indicators), and keep
  state across restarts with a [durable state store](guides/state-stores.md).
- Before going live, review the [production checklist](guides/production.md).
