"""Cancellation propagation, consistent state, and graceful engine shutdown."""

from __future__ import annotations

import asyncio

from common import MemorySource

from refresh_engine import (
    PlanAction,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


async def main() -> None:
    started = asyncio.Event()

    async def slow_operation(
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        started.set()
        await asyncio.Event().wait()

    engine = RefreshEngine(MemorySource({"resource": "new"}), slow_operation)
    refresh = asyncio.create_task(engine.refresh())
    await started.wait()
    refresh.cancel()
    cancelled = False
    try:
        await refresh
    except asyncio.CancelledError:
        cancelled = True
    assert cancelled
    assert await engine.store.load_all() == {}
    print("cancellation: propagated=True, committed_states=0")
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
