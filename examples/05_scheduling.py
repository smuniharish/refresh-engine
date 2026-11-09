"""Interval scheduling and requests from an async external scheduler."""

import asyncio

from common import DictionarySource, Recorder

from refresh_engine import (
    AsyncScheduler,
    RefreshEngine,
    RefreshRequest,
    RefreshResult,
    TriggerSource,
)


async def main() -> None:
    source = DictionarySource({"alpha": 1})
    async with RefreshEngine(source, Recorder()) as engine:
        completed = asyncio.Event()

        async def scheduled_refresh() -> RefreshResult:
            result = await engine.refresh(trigger=TriggerSource.SCHEDULED)
            completed.set()
            return result

        scheduler = AsyncScheduler(
            scheduled_refresh, interval=300, run_immediately=True
        )
        async with scheduler:
            await asyncio.wait_for(completed.wait(), timeout=5)
            print(f"async scheduler:    state={scheduler.state}")
        print(f"after leaving:      state={scheduler.state}")

        result = await engine.submit(RefreshRequest(trigger=TriggerSource.EXTERNAL))
        print(
            f"external async job: status={result.status}, "
            f"trigger={result.request.trigger}"
        )


if __name__ == "__main__":
    asyncio.run(main())
