"""Async interval and externally owned scheduler scenarios."""

import asyncio

from common import MemorySource, Recorder

from refresh_engine import (
    AsyncScheduler,
    ExternalSchedulerAdapter,
    RefreshEngine,
    RefreshRequest,
    TriggerSource,
)


async def main() -> None:
    engine = RefreshEngine(MemorySource({"alpha": 1}), Recorder())
    ran = asyncio.Event()

    async def scheduled():
        result = await engine.refresh(trigger=TriggerSource.SCHEDULED)
        ran.set()
        return result

    scheduler = AsyncScheduler(scheduled, interval=60, run_immediately=True)
    await scheduler.start()
    await asyncio.wait_for(ran.wait(), timeout=1)
    await scheduler.stop()

    external = ExternalSchedulerAdapter(engine.submit)
    result = await external.run(RefreshRequest(trigger=TriggerSource.EXTERNAL))
    print(f"external refresh: status={result.status.value}")
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
