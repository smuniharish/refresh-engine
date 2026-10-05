# Refresh modes and targeting

## Choosing a mode

| Mode | Refreshes | Typical use |
| --- | --- | --- |
| `INCREMENTAL` (default) | Added, modified, and dependency-changed resources; deletes resources missing from a complete discovery. | Routine scheduled refreshes. |
| `DEPENDENCY_AWARE` | Everything `INCREMENTAL` does, plus every discovered resource that transitively depends on a change or deletion. | Derived data, such as indexes, reports, or bundles built from other resources. |
| `FULL` | Every discovered resource, whether or not it changed; deletes resources missing from a complete discovery. | Rebuilding a destination, or after changing how operations transform data. |
| `TARGETED` | Only the resources chosen by IDs or a selector, whether or not they changed. | Webhooks, user-requested refreshes, and repairs. |

`INCREMENTAL` and `DEPENDENCY_AWARE` reuse stored fingerprints when a reliable cheap
indicator is unchanged. `FULL` and `TARGETED` read a fresh snapshot of every
resource they refresh.

```python
from refresh_engine import RefreshMode

await engine.refresh()
await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
await engine.refresh(mode=RefreshMode.FULL)
```

## Targeted refreshes

Pass resource IDs, a selector, or both. The mode defaults to `TARGETED` when either
is given:

```python
from refresh_engine import IDSelector, TagSelector

await engine.refresh(resource_ids=["invoice-17"])
await engine.refresh(selector=TagSelector("billing"))
await engine.refresh(resource_ids=["invoice-17"], selector=TagSelector("urgent"))
```

A targeted refresh observes only the targeted resources, so it stays cheap in large
populations; the other discovered resources count as skipped. A targeted ID that a
complete discovery no longer reports, but that has stored state, is deleted. IDs that
are neither discovered nor stored are listed in the result's warnings.

### Selectors

Selectors decide which discovered resources a targeted refresh includes:

| Selector | Selects |
| --- | --- |
| [`IDSelector`][refresh_engine.IDSelector] | Resources with one of the given IDs. |
| [`TagSelector`][refresh_engine.TagSelector] | Resources whose metadata tags include all (or, with `match_all=False`, any) of the given tags. |
| [`PredicateSelector`][refresh_engine.PredicateSelector] | Resources for which a function returns a truthy value. |

Combine selectors with `&` (both), `|` (either), and `~` (not):

```python
from refresh_engine import IDSelector, PredicateSelector, TagSelector

stale_billing = TagSelector("billing") & PredicateSelector(
    lambda resource: resource.version != "2026"
)
everything_but_drafts = ~TagSelector("draft")
billing_or_invoice = TagSelector("billing") | IDSelector(["invoice-17"])
```

Any object with a `matches(resource)` method works as a selector, and
[`ResourceSelectorABC`][refresh_engine.ResourceSelectorABC] adds the operators to
your own selectors. A selector that matches no discovered resource produces a
warning.

## Requests

`refresh()` builds a [`RefreshRequest`][refresh_engine.RefreshRequest] and submits
it. Build requests yourself to pass them around, for example from an event
handler:

```python
from refresh_engine import RefreshMode, RefreshRequest, TriggerSource

request = RefreshRequest(
    mode=RefreshMode.TARGETED,
    resource_ids={"invoice-17"},
    trigger=TriggerSource.EVENT,
    metadata={"webhook": "invoice.updated"},
    correlation_id="evt_8f2a",
)
result = await engine.submit(request)
```

The `trigger`, `metadata`, and `correlation_id` appear in lifecycle events, so logs
and metrics can tell refreshes apart.

## Example

```python
--8<--
examples/02_refresh_modes.py
--8<--
```

```text
--8<--
examples/output/02_refresh_modes.txt
--8<--
```
