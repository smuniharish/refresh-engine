# Example: scheduled and external refresh

Source: `examples/04_async_and_external_scheduling.py` (runnable as-is).

```python
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
```

## Verified output

```text
external refresh: status=success
```

## What this demonstrates

- `AsyncScheduler(callback, interval, run_immediately=True)` fires once
  immediately (useful to avoid waiting a full interval before the first
  refresh in tests/demos), then every `interval` seconds.
- `await scheduler.start()` returns as soon as the schedule is installed —
  the caller's `main()` is never blocked waiting for a refresh to finish.
- `scheduler.stop()` cleanly tears down the background task.
- `ExternalSchedulerAdapter(engine.submit)` is a one-line integration point
  for any externally owned scheduler (cron, Kubernetes `CronJob`, Celery
  beat) — it does not run its own timer at all.

## Production shutdown ordering

```python
try:
    await scheduler.start()
    await shutdown_requested.wait()
finally:
    await scheduler.stop()
    await engine.close()
```

Stop the scheduler before closing its engine. See
[../references/scheduling.md](../references/scheduling.md) and
[../references/failure-handling.md](../references/failure-handling.md) for
`cancel_active_on_stop` / `cancel_active_on_shutdown` semantics.

## Mapping external triggers (webhooks, queries) to a refresh

```python
from refresh_engine import QueryTrigger

query_request = QueryTrigger().request(["beta"], metadata={"caller": "webhook"})
result = await engine.submit(query_request)
```

`QueryTrigger().request(ids)` always builds a `RefreshMode.TARGETED`,
`TriggerSource.QUERY` request — combine this with `ExternalSchedulerAdapter`
or call `engine.submit()` directly from a webhook handler.
