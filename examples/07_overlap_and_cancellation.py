"""Overlap policies, cancellation, and graceful shutdown."""

import asyncio

from common import DictionarySource

from refresh_engine import (
    OverlapPolicy,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshMode,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


class SlowDestination:
    """Block every write until `release` is set, counting started writes."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.writes = 0

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        self.writes += 1
        self.started.set()
        await self.release.wait()


async def overlap(policy: OverlapPolicy) -> str:
    destination = SlowDestination()
    engine = RefreshEngine(
        DictionarySource({"alpha": 1}),
        destination,
        config=RefreshConfig(overlap_policy=policy),
    )
    running = asyncio.create_task(engine.refresh())
    await destination.started.wait()
    newer = [
        asyncio.create_task(engine.refresh(mode=RefreshMode.FULL)) for _ in range(2)
    ]
    await asyncio.sleep(0)
    destination.release.set()
    results = await asyncio.gather(running, *newer)
    await engine.close()
    statuses = ", ".join(str(result.status) for result in results)
    return f"{statuses} (refreshes started: {destination.writes})"


async def main() -> None:
    for policy in OverlapPolicy:
        print(f"{policy + ':':18}{await overlap(policy)}")

    destination = SlowDestination()
    engine = RefreshEngine(DictionarySource({"alpha": 1}), destination)
    caller = asyncio.create_task(engine.refresh())
    await destination.started.wait()
    caller.cancel()
    try:
        await caller
    except asyncio.CancelledError:
        committed = len(await engine.store.load_all())
        print(f"{'caller cancelled:':18}committed states={committed}")
    await engine.close()

    destination = SlowDestination()
    engine = RefreshEngine(DictionarySource({"alpha": 1}), destination)
    running = asyncio.create_task(engine.refresh())
    await destination.started.wait()
    await engine.close(cancel=True)
    result = await running
    print(f"{'shutdown:':18}{result.status}, {result.warnings[0]}")


if __name__ == "__main__":
    asyncio.run(main())
