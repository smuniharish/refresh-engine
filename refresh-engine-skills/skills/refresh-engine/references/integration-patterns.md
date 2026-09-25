# Integration patterns

Source: synthesized from `README.md`, `docs/architecture/overview.md`,
`examples/*.py`. Every pattern below is domain-neutral — none reference any
specific resource domain (MCP, agents, files, etc.); that translation belongs
entirely in your `ResourceSource`/`RefreshOperation`.

## Pattern A — Basic refresh

```
Application -> ResourceSource + RefreshOperation -> RefreshEngine -> RefreshResult
```

Use when: a single one-shot or manually triggered refresh is enough (no
scheduling, no dependency modeling). See [../examples/basic.md](../examples/basic.md).

## Pattern B — Incremental refresh

```
Source -> snapshot -> FingerprintStrategy -> ChangeDetector -> ChangeSet -> RefreshOperation (changed only)
```

Use when: the resource population is large or the operation is expensive, and
most resources are unchanged between refreshes. This is the default
(`RefreshMode.INCREMENTAL`) — you get it for free by doing nothing extra,
though you may want a reliable `cheap_indicator` to avoid snapshotting
unchanged resources too. See [../examples/incremental.md](../examples/incremental.md)
and [incremental-refresh.md](incremental-refresh.md).

## Pattern C — Scheduled refresh

```
Application -> AsyncScheduler (background, same event loop) -> engine.refresh(trigger=SCHEDULED)
            -> or: external scheduler -> ExternalSchedulerAdapter -> engine.submit(request)
```

Use when: refresh must happen periodically without blocking the caller's main
execution path. See [../examples/scheduled.md](../examples/scheduled.md) and
[scheduling.md](scheduling.md).

## Pattern D — Dependency-aware refresh

```
Resource graph (Resource.dependencies) -> DependencyGraph -> change -> ImpactAnalyzer -> ImpactSet -> RefreshPlanner -> ordered RefreshPlan
```

Use when: a change in one resource must propagate to resources that depend on
it (shared configuration, computed/derived resources, transitive references).
See [../examples/dependency-aware.md](../examples/dependency-aware.md) and
[dependency-refresh.md](dependency-refresh.md).

## Pattern E — Consumer-specific integration (generic resource adapter)

The engine has no notion of your resource domain. A minimal, fully generic
adapter shape:

```python
from collections.abc import AsyncIterator
from refresh_engine import (
    DiscoveryResult, PlanAction, RefreshOperationABC, Resource,
    ResourceSnapshot, ResourceSourceABC,
)

class MySystemSource(ResourceSourceABC):
    """Translate *your* domain's list/read operations into Resource/ResourceSnapshot."""

    def __init__(self, client) -> None:
        self._client = client

    async def discover(self) -> DiscoveryResult:
        async def resources() -> AsyncIterator[Resource]:
            async for item in self._client.list_items():
                yield Resource(
                    item.id,
                    dependencies=frozenset(item.depends_on),
                    cheap_indicator=item.etag,
                    cheap_indicator_reliable=True,  # only if item.etag truly proves no change
                )

        return DiscoveryResult(resources(), complete=True)

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        item = await self._client.get_item(resource.resource_id)
        return ResourceSnapshot(
            resource.resource_id,
            content=item.body,
            etag=item.etag,
            dependencies=resource.dependencies,
        )


class MySystemOperation(RefreshOperationABC):
    """Translate REFRESH/DELETE plan actions into your domain's write/delete calls."""

    def __init__(self, destination) -> None:
        self._destination = destination

    async def __call__(self, resource, snapshot, action, request) -> None:
        if action is PlanAction.DELETE:
            assert resource is not None
            await self._destination.remove(resource.resource_id)
            return
        assert resource is not None and snapshot is not None
        await self._destination.upsert(resource.resource_id, snapshot.content)
```

Keep this adapter as the **only** place your domain's concepts (MCP tools,
agent skills, catalog entries, whatever they are) touch `refresh_engine`
types. Do not leak `Resource`/`ResourceSnapshot` construction logic into
unrelated application layers, and do not build a parallel, domain-specific
refresh/scheduling/hashing framework alongside this one — extend via the
documented protocols/ABCs instead (custom `FingerprintStrategy`,
`ResourceSelector`, `StateStore`, etc.).

## Choosing between patterns

Patterns compose — a real integration is usually B + C (incremental +
scheduled), or B + C + D (add dependency-awareness) all wrapped in one E-style
adapter pair. There is no pattern that requires bypassing `RefreshEngine` or
reimplementing its stages.
