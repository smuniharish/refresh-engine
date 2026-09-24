# Sources and operations

`discover()` returns `DiscoveryResult(resources=<async iterator>, complete=<bool>)`.
Discovery should yield lightweight `Resource` values with stable IDs. `snapshot()`
performs the more expensive read and returns the values used for fingerprinting and
the operation.

The operation signature is:

```python
from refresh_engine import PlanAction, RefreshRequest, Resource, ResourceSnapshot


async def operation(
    resource: Resource | None,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
) -> None:
    if action is PlanAction.DELETE:
        assert resource is not None
        await destination.delete(resource.resource_id)
        return

    assert resource is not None
    assert snapshot is not None
    await destination.upsert(resource.resource_id, snapshot.content)
```

For `REFRESH`, `resource` and `snapshot` identify current source data. For `DELETE`,
the engine supplies a lightweight `Resource` carrying the ID and no snapshot; do not
expect source metadata or content. Operations should be idempotent,
cancellation-cooperative, and safe to retry.
Raise an exception to prevent state advancement for that resource.

A reliable `Resource.cheap_indicator` with `cheap_indicator_reliable=True` lets the
engine avoid snapshotting when it matches successful stored state. Never label a
hint reliable unless equality proves the snapshot fingerprint cannot have changed.
