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
