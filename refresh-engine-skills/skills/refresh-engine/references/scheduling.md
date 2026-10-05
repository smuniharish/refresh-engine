# Scheduling, overlap, and shutdown

## Interval refreshes

`AsyncScheduler` runs a coroutine at a fixed interval on the running event loop:

```python
from refresh_engine import AsyncScheduler, RefreshEngine, TriggerSource

engine = RefreshEngine(source, operation, store=store)
scheduler = AsyncScheduler(
    lambda: engine.refresh(trigger=TriggerSource.SCHEDULED),
    interval=300,
    run_immediately=True,
)

async with engine, scheduler:
    await shutdown_requested.wait()
```

- Runs follow a fixed grid measured from `start()`. A slow run skips the missed
  slots instead of running back to back, and runs never overlap.
- `pause()` skips runs until `resume()`. `stop()` waits until the scheduler has
  stopped, including an active run, and `stop(cancel=True)` cancels the run.
  Stopping is idempotent. From the callback itself, `stop()` never waits; from code
  that a scheduled run may wait for, such as an operation of the engine it
  refreshes, use `stop(wait=False)`. `state` is a `SchedulerState`.
- A callback exception is logged to the `refresh_engine` logger, published as
  `SCHEDULER_RUN_FAILED`, and the schedule continues. A failed refresh is not an
  exception; inspect results in the callback if you need to react to them.
- `async with engine, scheduler:` starts the scheduler, and on exit stops it before
  closing the engine.

Inside a web application, tie both to the application's lifespan. For example,
with FastAPI:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from refresh_engine import AsyncScheduler, RefreshEngine, TriggerSource


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = RefreshEngine(source, operation, store=store)
    scheduler = AsyncScheduler(
        lambda: engine.refresh(trigger=TriggerSource.SCHEDULED), interval=300
    )
    async with engine, scheduler:
        app.state.engine = engine
        yield


app = FastAPI(lifespan=lifespan)


@app.post("/refresh/{resource_id}")
async def refresh_resource(resource_id: str) -> dict[str, str]:
    result = await app.state.engine.refresh(
        resource_ids=resource_id, trigger=TriggerSource.QUERY
    )
    return {"status": result.status}
```

## External schedulers and other threads

Use `ExternalSchedulerAdapter` when refreshes are triggered outside the event
loop, for example by a thread-based job scheduler. Create it on the engine's loop:

```python
from refresh_engine import ExternalSchedulerAdapter

adapter = ExternalSchedulerAdapter(engine.submit)

result = await adapter.run()  # on the event loop
result = adapter.run_threadsafe(timeout=600)  # from another thread, blocking
future = adapter.submit_threadsafe()  # from another thread, non-blocking
```

- Without a request argument, the adapter submits an `INCREMENTAL` refresh with
  the trigger `EXTERNAL`; pass a `RefreshRequest` to choose another.
- `run_threadsafe()` raises `RuntimeError` on the loop's own thread, because it
  would deadlock, and `TimeoutError` when `timeout` expires.
- For cron-style batch jobs, skip the adapter: run a script that calls
  `asyncio.run()` around one `refresh()`, with a durable state store.

## Overlapping requests

An engine runs one refresh at a time. `RefreshConfig(overlap_policy=...)` decides
what happens to requests that arrive meanwhile:

| Policy | New request while a refresh runs |
| --- | --- |
| `COALESCE` (default) | Merges into a queued request that covers it, or else queues. Merged callers share one result. |
| `QUEUE` | Queues and runs separately, in order. |
| `SKIP_IF_RUNNING` | Returns a `SKIPPED` result immediately. |
| `CANCEL_PREVIOUS` | Cancels the running and queued refreshes, whose callers get `CANCELLED` results, and runs next. |

Coalescing never drops work: `FULL` covers every mode, `DEPENDENCY_AWARE` covers
`INCREMENTAL`, and targeted requests merge into one that targets the union. An
incremental and a targeted request run separately.

## Cancellation and shutdown

- Cancelling the task that awaits `refresh()` cancels the refresh once no other
  caller waits for it, and raises `asyncio.CancelledError` in that task. Nothing is
  committed for a cancelled refresh.
- `close()` stops accepting requests, finishes running and queued refreshes, and
  delivers pending events. `close(cancel=True)` cancels them, and their callers get
  `CANCELLED` results. `close(wait=False)` returns without waiting.
- Requests after `close()` raise `EngineClosedError`.
- Leaving `async with engine:` closes the engine, cancelling active work if the
  block raised.
- An engine belongs to one event loop. Create it inside the running loop, and do
  not share it across loops or threads; use `ExternalSchedulerAdapter` for other
  threads.
- A source, operation, fingerprint strategy, or state store must not wait for a
  refresh of its own engine. Awaited directly there, `refresh()` raises
  `RuntimeError` and `close()` returns without waiting; waits routed through another
  task, such as `asyncio.gather`, never finish. Start follow-up refreshes with
  `asyncio.create_task(engine.refresh(...))` without awaiting the task, and shut
  down from such code with `close(wait=False)` or `stop(wait=False)`.
- Event handlers run outside refreshes; they may await `refresh()` and `close()`
  directly.
