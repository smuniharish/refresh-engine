# Sources and operations

## Contents

- [The source](#the-source)
- [The operation](#the-operation)
- [Protocols or base classes](#protocols-or-base-classes)
- [Domain adapters](#domain-adapters)

## The source

A source is any object with two coroutine methods:

```python
from collections.abc import AsyncIterator

from refresh_engine import DiscoveryResult, Resource, ResourceSnapshot


class ArticleSource:
    def __init__(self, client: CmsClient) -> None:
        self.client = client

    async def discover(self) -> DiscoveryResult:
        async def listing() -> AsyncIterator[Resource]:
            async for page in self.client.list_articles():
                for item in page:
                    yield Resource(
                        item.id,
                        metadata={"tags": item.tags},
                        cheap_indicator=item.etag,
                        cheap_indicator_reliable=True,
                    )

        return DiscoveryResult(listing())

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        article = await self.client.get_article(resource.resource_id)
        return ResourceSnapshot(
            resource.resource_id,
            content={"title": article.title, "body": article.body},
            etag=article.etag,
        )
```

### discover()

- Return `DiscoveryResult(resources, complete=True, diagnostics=())`. `resources`
  may be a list, any iterable, or an async iterable, which the engine consumes as a
  stream; use one for paginated APIs.
- Set `complete=False` whenever the listing may omit existing resources, for
  example after a page limit or with a filtered query. The engine then infers no
  deletions and reports the suppressed deletions in `result.warnings`.
- Raise when the system of record is unavailable. The refresh then fails before
  change detection, and nothing is refreshed, deleted, or written.
- Resource IDs must be unique, non-empty strings that stay stable across runs, such
  as primary keys, relative paths, or URLs. Never use list positions or random
  UUIDs.
- A discovery fails if it returns something other than a `DiscoveryResult`, yields
  something other than a `Resource`, or yields the same ID twice.

`Resource` fields:

| Field | Meaning |
| --- | --- |
| `resource_id` | Stable unique ID. |
| `metadata` | Data available without reading the resource, such as tags for selectors. Not fingerprinted. |
| `version` | Optional version label. |
| `dependencies` | IDs this resource depends on, or `None` when discovery does not know them. |
| `cheap_indicator`, `cheap_indicator_reliable` | An inexpensive change signal; see [change-detection.md](change-detection.md). |

### snapshot()

- Return a `ResourceSnapshot` with the same `resource_id`. A different ID fails the
  resource.
- `content` is passed to the operation. The default fingerprint covers `content`,
  `metadata`, `version`, `version_token`, and `etag`.
- `version` overrides the discovery version. `dependencies=None` keeps the
  dependencies declared by discovery; a set replaces them.
- A raised exception fails only this resource. Its previous state is kept, and the
  next refresh reads it again.
- Snapshots are read concurrently, up to `RefreshConfig.max_concurrency`.
- Run blocking calls in a thread:
  `content = await asyncio.to_thread(path.read_bytes)`.

## The operation

An operation is a callable with this positional-only signature:

```python
from refresh_engine import PlanAction, RefreshRequest, Resource, ResourceSnapshot


async def update_index(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    if action is PlanAction.DELETE:
        await index.delete(resource.resource_id, missing_ok=True)
    elif snapshot is not None:
        await index.upsert(resource.resource_id, snapshot.content)
```

- `PlanAction.REFRESH`: `resource` is the discovered resource and `snapshot` its
  current snapshot.
- `PlanAction.DELETE`: `resource` carries only the deleted ID, and `snapshot` is
  `None`.
- `request` is the `RefreshRequest` that planned the operation; it carries `mode`,
  `trigger`, `metadata`, and `correlation_id`.
- Operations run in dependency order, up to `max_concurrency` at a time.
- Raise to report a failure. Configure retries and timeouts with `RetryConfig` and
  `TimeoutConfig` instead of retrying inside the operation.
- Write idempotently, with upserts, delete-if-present, or idempotency keys.
  `except Exception` is fine; never use a bare `except:` or catch `BaseException`,
  which would swallow cancellation.
- Never wait for a refresh of the same engine from the operation: awaited directly,
  it raises `RuntimeError`, and through another task it never finishes. Start
  follow-up refreshes in a separate task that the operation does not await.
- The source, operation, fingerprint strategy, and state store run in a copy of
  the context of the code that requested the refresh, so context variables such as
  tracing spans reach them; merged requests use the first request's context.

An operation that needs clients or configuration can be a class:

```python
from refresh_engine import RefreshOperationABC


class IndexWriter(RefreshOperationABC):
    def __init__(self, index: SearchIndex) -> None:
        self.index = index

    async def __call__(self, resource, snapshot, action, request, /) -> None:
        if action is PlanAction.DELETE:
            await self.index.delete(resource.resource_id, missing_ok=True)
        elif snapshot is not None:
            await self.index.upsert(resource.resource_id, snapshot.content)
```

## Protocols or base classes

Every extension point exists twice: as a protocol, for example `ResourceSource`,
which any object with the right methods satisfies, and as an abstract base class,
for example `ResourceSourceABC`, which rejects incomplete implementations when they
are instantiated. Protocol parameters are positional-only, so implementations may
name their parameters freely. The engine raises `TypeError` at construction when an
argument does not implement its contract.

## Domain adapters

To build a domain-specific refresher, for example for plugins, agent tools, or
documents, put all domain knowledge into the source and the operation:

- map each domain object to one `Resource` with a stable ID;
- put what the destination needs into `ResourceSnapshot.content`;
- express relationships as `dependencies`;
- keep the engine, its configuration, and its state store generic.

Do not fork, wrap, or re-implement change detection, ordering, retries, or
scheduling around the engine.
