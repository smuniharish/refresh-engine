# Refresh modes and targeting

## Modes

| Mode | Refreshes | Deletes | Reads snapshots |
| --- | --- | --- | --- |
| `INCREMENTAL` (default) | Added, modified, and dependency-changed resources. | Resources missing from a complete discovery. | Only for resources without a matching reliable cheap indicator. |
| `DEPENDENCY_AWARE` | Everything `INCREMENTAL` refreshes, plus every discovered resource that transitively depends on a change or deletion. | Like `INCREMENTAL`. | Like `INCREMENTAL`, plus impacted resources just before their operation. |
| `FULL` | Every discovered resource. | Like `INCREMENTAL`. | Always. |
| `TARGETED` | Only the resources chosen by IDs or a selector, whether or not they changed. | Targeted IDs that have stored state but are missing from a complete discovery. | Only for targeted resources. |

Choose:

- `INCREMENTAL` for routine scheduled refreshes;
- `DEPENDENCY_AWARE` when outputs are built from other resources, such as
  indexes, bundles, or reports;
- `FULL` to rebuild a destination, or after changing what an operation produces;
- `TARGETED` for webhooks, user-requested refreshes, and repairs.

```python
from refresh_engine import RefreshMode

await engine.refresh()
await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
await engine.refresh(mode=RefreshMode.FULL)
```

## Targeting

Passing `resource_ids`, `selector`, or both makes the refresh `TARGETED`. A single
string is one ID.

```python
from refresh_engine import IDSelector, PredicateSelector, TagSelector

await engine.refresh(resource_ids="invoice-17")
await engine.refresh(selector=TagSelector("billing"))
await engine.refresh(selector=TagSelector(["billing", "eu"], match_all=False))
await engine.refresh(
    selector=TagSelector("billing") & ~PredicateSelector(lambda r: r.version == "v2")
)
await engine.refresh(selector=TagSelector("urgent") | IDSelector(["invoice-17"]))
```

- `TagSelector` reads tags from `Resource.metadata["tags"]`, or from another key
  given as `key=`. The value may be one string or an iterable of strings.
- Any object with a `matches(resource)` method is a selector. Subclass
  `ResourceSelectorABC` to support `&`, `|`, and `~`.
- IDs that are neither discovered nor stored, and selectors that match nothing,
  produce warnings in `result.warnings`.
- Resources that are not targeted count as skipped and are not read.
- `resource_ids` and `selector` are only valid in `TARGETED` mode; combining them
  with another mode raises `ConfigurationError`.

## Requests

`refresh()` builds a `RefreshRequest`. Build one yourself to pass it between
components, then run it with `engine.submit(request)`:

```python
from refresh_engine import RefreshMode, RefreshRequest, TriggerSource

request = RefreshRequest(
    mode=RefreshMode.TARGETED,
    resource_ids={"invoice-17"},
    trigger=TriggerSource.EVENT,
    metadata={"webhook": "invoice.updated"},
    correlation_id=delivery_id,
)
result = await engine.submit(request)
```

- `trigger`: `MANUAL`, `SCHEDULED`, `EVENT`, `QUERY`, or `EXTERNAL`. It is used
  only for observability.
- `metadata`: application data, published in the `REFRESH_STARTED` event. Keep
  secrets out of it.
- `correlation_id`: propagated to every lifecycle event of the refresh, which ties
  logs to the originating webhook, job, or HTTP request.
- `result.request` is the request that ran, which is a merged request when
  overlapping requests were coalesced; see [scheduling.md](scheduling.md).
