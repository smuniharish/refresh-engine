# Getting started

Install the package into a Python 3.12 environment:

```console
uv add refresh-engine
```

Alternatively, with pip:

```console
python -m pip install refresh-engine
```

Then implement two async contracts: `ResourceSource` and `RefreshOperation`.

```python
import asyncio
from refresh_engine import (
    DiscoveryResult, RefreshEngine, Resource, ResourceSnapshot,
)

class Source:
    async def discover(self):
        async def stream():
            yield Resource("one", metadata={"group": "demo"})
        return DiscoveryResult(stream(), complete=True)

    async def snapshot(self, resource):
        return ResourceSnapshot(
            resource.resource_id,
            content={"payload": 42},
            metadata=resource.metadata,
        )

async def operation(resource, snapshot, action, request):
    print(action.value, resource.resource_id if resource else "deleted resource")

async def main():
    engine = RefreshEngine(Source(), operation)
    result = await engine.refresh()
    print(result.status.value, result.refreshed_count)
    await engine.close()

asyncio.run(main())
```

Output:

```text
refresh one
success 1
```

The first successful run records fingerprints. A subsequent incremental run skips
unchanged resources. Keep resource IDs stable, fingerprints deterministic, and the
operation idempotent. Always call `close()` during async shutdown. For a synchronous
host, submit requests through an externally owned scheduler adapter.

Next read [Sources and operations](guides/sources-and-operations.md) and
[State and failure safety](guides/state-and-failure-safety.md), or continue with the
[examples](guides/examples-and-results.md).
