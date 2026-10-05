# Sources and operations

Every integration provides a **source**, which lists and reads resources, and an
**operation**, which applies changes to a destination. Both are plain objects with
coroutine methods; no base class is required.

## Implementing a source

```python
from collections.abc import AsyncIterator

from refresh_engine import DiscoveryResult, Resource, ResourceSnapshot


class DocumentSource:
    def __init__(self, client: DocumentClient) -> None:
        self.client = client

    async def discover(self) -> DiscoveryResult:
        async def listing() -> AsyncIterator[Resource]:
            async for page in self.client.pages():
                for item in page.items:
                    yield Resource(
                        item.id,
                        metadata={"tags": item.tags},
                        cheap_indicator=item.etag,
                        cheap_indicator_reliable=True,
                    )

        return DiscoveryResult(listing(), complete=True)

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        document = await self.client.fetch(resource.resource_id)
        return ResourceSnapshot(
            resource.resource_id,
            content=document.body,
            metadata={"title": document.title},
            etag=document.etag,
        )
```

### Discovery

`discover()` returns a [`DiscoveryResult`][refresh_engine.DiscoveryResult]:

- `resources` may be a list or any iterable, or an asynchronous iterable that the
  engine consumes as a stream.
- `complete` states whether the listing is known to contain every resource. Only a
  complete discovery allows deletions to be inferred.
- `diagnostics` are notes that appear in the result's warnings.

Resource IDs must be unique non-empty strings. The engine reports the discovery as
failed, and changes nothing, if `discover()` raises, returns something other than
a `DiscoveryResult`, yields something other than a `Resource`, or yields the same ID
twice.

!!! tip "Raise instead of returning an empty listing"
    When the backing system is unavailable, raise an exception. An empty, complete
    discovery means that every resource was deleted.

### Snapshots

`snapshot()` reads one discovered resource and returns a
[`ResourceSnapshot`][refresh_engine.ResourceSnapshot] with the same `resource_id`.
Its `content` is passed to the operation and, together with `metadata`, `version`,
`version_token`, and `etag`, fingerprinted by the default strategy. A snapshot may
also report `dependencies`; when it leaves them as `None`, the dependencies declared
by discovery apply.

When a snapshot fails, only that resource fails: its previous state is kept and the
failure is reported with the phase `snapshot`.

### Blocking I/O

The engine runs on the event loop. Run blocking calls, such as file system access or
synchronous database drivers, in a worker thread:

```python
async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
    path = self.root / resource.resource_id
    content = await asyncio.to_thread(path.read_bytes)
    return ResourceSnapshot(resource.resource_id, content=content)
```

[Example 09](../examples.md#files-as-resources) indexes a directory this way.

## Implementing an operation

An operation is any callable with this signature:

```python
from refresh_engine import PlanAction, RefreshRequest, Resource, ResourceSnapshot


async def publish(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    if action is PlanAction.DELETE:
        await destination.delete(resource.resource_id)
    else:
        assert snapshot is not None
        await destination.upsert(resource.resource_id, snapshot.content)
```

- For `PlanAction.REFRESH`, `resource` is the discovered resource and `snapshot` is
  its current snapshot.
- For `PlanAction.DELETE`, `resource` carries only the ID of the deleted resource
  and `snapshot` is `None`.
- `request` is the [`RefreshRequest`][refresh_engine.RefreshRequest] that planned the
  operation, including its trigger, metadata, and correlation ID.

Raise an exception to report a failure. The resource's state is not advanced, so the
next refresh tries again. Configure [retries and timeouts](failure-handling.md) on
the engine rather than inside the operation.

!!! warning "Make operations idempotent and cancellation-friendly"
    An operation can run more than once for the same change: after a retry, after a
    timeout whose work completed anyway, or after a restart. Write destinations with
    upserts or idempotency keys, and never swallow `asyncio.CancelledError`.

## Context variables

The source, operation, fingerprint strategy, and state store of a refresh run in a
copy of the context of the code that requested the refresh. Context variables, such
as an OpenTelemetry span or fields bound with `structlog.contextvars`, therefore
reach them even when the request waited behind other refreshes. When the overlap
policy merges several requests into one refresh, it runs in the context of the
first request.

Do not wait for a refresh of the same engine from these components: the refresh they
belong to cannot finish before they return. Awaited directly, `refresh()` raises
`RuntimeError` there. See
[Calling the engine from its own refreshes](scheduling.md#calling-the-engine-from-its-own-refreshes).

## Protocols or base classes

Sources, operations, and every other extension point are defined twice:

- as protocols, such as [`ResourceSource`][refresh_engine.ResourceSource], so any
  object with the right methods works;
- as abstract base classes, such as
  [`ResourceSourceABC`][refresh_engine.ResourceSourceABC], which reject incomplete
  implementations at construction time.

Protocol parameters are positional-only, so your implementations may name their
parameters freely. [Example 12](../examples.md#abstract-base-classes) uses the base
classes throughout.
