# refresh-engine

`refresh-engine` is an asyncio-first, domain-neutral Python library for discovering
resources, detecting changes, planning dependency-aware work, and executing
application-defined refresh operations safely.

## Features

- full, incremental, targeted, and dependency-aware refresh modes
- deterministic fingerprints and reliable cheap pre-checks
- bounded concurrency, retries, timeouts, and overlap policies
- transactional state with an in-memory default and application-injected durable stores
- asyncio and externally managed scheduling
- typed public protocols and ABC inheritance points
- native `structlog` lifecycle logging with configurable processors and redaction

Requires Python 3.12.

`RefreshEngine` uses `InMemoryStateStore` when `store` is omitted. Applications can
inject a SQLite, PostgreSQL, or other adapter through the public `StateStore`
protocol or `StateStoreABC`:

```python
engine = RefreshEngine(source, operation, store=my_store)
```

SQLite and PostgreSQL reference adapters are provided under
[`examples/`](examples/README.md); database clients are not part of the production
package.

## Quick start

```python
import asyncio
from refresh_engine import (
    DiscoveryResult, PlanAction, RefreshEngine, Resource, ResourceSnapshot,
)

class Source:
    async def discover(self):
        async def resources():
            yield Resource("alpha")
        return DiscoveryResult(resources())

    async def snapshot(self, resource):
        return ResourceSnapshot(resource.resource_id, content={"value": 1})

async def apply(resource, snapshot, action, request):
    assert resource is not None
    print(action, resource.resource_id)

async def main():
    engine = RefreshEngine(Source(), apply)
    result = await engine.refresh()
    print(result.status, result.refreshed_count)
    await engine.close()

asyncio.run(main())
```

For a `PlanAction.DELETE`, the operation receives a lightweight `Resource` carrying
the deleted ID and `snapshot=None`; deletion handling must not expect source payloads.

## Documentation

- [Getting started](docs/getting-started.md)
- [Guides](docs/guides/index.md)
- [API reference](docs/api/index.md)
- [Architecture](docs/architecture/overview.md)
- [Runnable examples](examples/README.md)
- [Benchmarks](benchmarks/README.md)

## Development and support

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), the
[changelog](CHANGELOG.md), and the [Apache 2.0 license](LICENSE).
