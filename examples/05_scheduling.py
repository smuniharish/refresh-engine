"""Interval scheduling, and refreshes requested from a thread-based scheduler."""

import asyncio
import threading

from common import DictionarySource, Recorder

from refresh_engine import (
    AsyncScheduler,
    ExternalSchedulerAdapter,
    RefreshEngine,
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

        adapter = ExternalSchedulerAdapter(engine.submit)
        results: list[RefreshResult] = []

        def thread_job() -> None:
            results.append(adapter.run_threadsafe(timeout=5))

        thread = threading.Thread(target=thread_job)
        thread.start()
        await asyncio.to_thread(thread.join)
        (result,) = results
        print(
            f"thread scheduler:   status={result.status}, "
            f"trigger={result.request.trigger}"
        )


if __name__ == "__main__":
    asyncio.run(main())
