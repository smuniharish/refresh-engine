# Example: custom extensions (ABCs, custom store, overlap policy)

Source: `examples/13_abc_extensions.py` and `examples/08_custom_store_and_overlap.py`
(both runnable as-is).

## Inheritance-based extension points

```python
"""Inheritance-based extensions using the optional abstract base class layer."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    RefreshOperationABC,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
    ResourceSourceABC,
)


class Source(ResourceSourceABC):
    async def discover(self) -> DiscoveryResult:
        async def resources() -> AsyncIterator[Resource]:
            yield Resource("configuration")

        return DiscoveryResult(resources())

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        return ResourceSnapshot(resource.resource_id, content={"enabled": True})


class Operation(RefreshOperationABC):
    async def __call__(
        self,
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        assert resource is not None
        print(action.value, resource.resource_id)


async def main() -> None:
    engine = RefreshEngine(Source(), Operation())
    assert (await engine.refresh()).refreshed_count == 1
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Verified output: `refresh configuration` (one `PlanAction.REFRESH` call for
the single discovered resource).

`ResourceSourceABC` and `RefreshOperationABC` give you `@abstractmethod`
enforcement at class-definition time — useful when a team wants an explicit
base class contract instead of relying purely on structural typing. Every ABC
in `refresh_engine.api.abc` also satisfies the corresponding protocol, so you
can mix ABC-based and protocol-only (plain duck-typed) implementations freely
within the same application.

## Custom `StateStore` adapter + overlap policy

```python
"""Custom store adapters and overlapping request policy scenarios."""
import asyncio

from common import MemorySource, Recorder

from refresh_engine import (
    InMemoryStateStore,
    OverlapPolicy,
    RefreshConfig,
    RefreshEngine,
)


class AuditedStore:
    """A structural StateStore adapter around any transactional backend."""

    def __init__(self) -> None:
        self.backend = InMemoryStateStore()
        self.loads = 0

    async def load_all(self):
        self.loads += 1
        return await self.backend.load_all()

    def transaction(self):
        return self.backend.transaction()


async def main() -> None:
    store = AuditedStore()
    engine = RefreshEngine(
        MemorySource({"alpha": 1}),
        Recorder(),
        store=store,
        config=RefreshConfig(overlap_policy=OverlapPolicy.COALESCE),
    )
    first, second = await asyncio.gather(engine.refresh(), engine.refresh())
    print(
        f"coalesced requests: first={first.status.value}, "
        f"second={second.status.value}, store_loads={store.loads}"
    )
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Verified output:

```text
coalesced requests: first=success, second=success, store_loads=1
```

`AuditedStore` satisfies `StateStore` **structurally** (no ABC inheritance
needed) by delegating `transaction()` to an underlying `InMemoryStateStore`
while adding its own instrumentation (`loads` counter) around `load_all()`.
Two concurrent `engine.refresh()` calls under `OverlapPolicy.COALESCE` merge
into a single underlying run — both callers get a successful result, and the
store is loaded only once.

## When to use ABCs vs. plain protocol objects

- Use the ABC (`ResourceSourceABC`, `RefreshOperationABC`, `StateStoreABC`,
  etc.) when you want `@abstractmethod` enforcement, a discoverable base
  class in your codebase, or a provided default (e.g.
  `StateTransactionABC.__aenter__`/`__aexit__`).
- Use a plain object/dataclass (protocol-only) when the type already exists
  in your codebase and you just need it to satisfy `ResourceSource`,
  `RefreshOperation`, `StateStore`, etc. structurally — no changes to its
  base classes required.

See [../references/architecture.md](../references/architecture.md) for the
full contract table.
