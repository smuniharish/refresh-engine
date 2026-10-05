---
hide:
  - navigation
---

# refresh-engine

**Keep a destination in sync with a changing collection of resources, and only do
the work that changed.**

refresh-engine is an asyncio-first Python library. It discovers your resources,
detects what changed since the last successful refresh, plans the work in
dependency order, and runs your refresh operation with bounded concurrency,
retries, and transactional state. It knows nothing about your domain: you describe
how to list and read resources and what to do with them, and the engine takes care
of everything in between.

![The refresh pipeline](assets/diagrams/pipeline-light.png#only-light)
![The refresh pipeline](assets/diagrams/pipeline-dark.png#only-dark)

<div class="grid cards" markdown>

-   :material-lightning-bolt:{ .lg .middle } **Incremental by default**

    ---

    Deterministic fingerprints skip every resource that did not change, and with
    reliable cheap indicators a refresh of 100,000 unchanged resources reads none
    of them.

-   :material-graph-outline:{ .lg .middle } **Dependency-aware**

    ---

    Changes propagate to dependent resources, operations run dependencies first,
    failures skip only the affected dependents, and cycles are isolated.

-   :material-shield-check-outline:{ .lg .middle } **Safe by design**

    ---

    A failed or partial discovery never deletes anything, failed operations never
    advance state, and resources that need another attempt are retried
    automatically.

-   :material-tune-variant:{ .lg .middle } **Production controls**

    ---

    Bounded concurrency, retries with backoff, per-phase timeouts, overlap
    policies, scheduling, graceful cancellation, structured logs, and metrics.

</div>

## Install

```bash
pip install refresh-engine
```

refresh-engine requires Python 3.12 or newer.

## A first refresh

Implement a source that lists and reads resources, and an operation that applies
each change. The engine does the rest:

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

Running the program prints:

```text
success: refreshed 2 {'apples': 120, 'pears': 80}
success: modified 1, deleted 1 {'pears': 75}
```

The second refresh touches only the modified and the deleted resource. The
[examples](examples.md) show every feature in action.

## Where to go next

- [Getting started](getting-started.md) builds a complete integration step by step.
- [Concepts](concepts.md) explains resources, fingerprints, state, and statuses.
- The [guides](guides/index.md) cover each capability in depth.
- The [API reference](api/index.md) documents every public name.
