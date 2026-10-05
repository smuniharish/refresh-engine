# Scheduling and overlap

## Interval scheduling

[`AsyncScheduler`][refresh_engine.AsyncScheduler] runs a coroutine at a fixed
interval on the running event loop, without blocking it:

```python
from refresh_engine import AsyncScheduler, TriggerSource

scheduler = AsyncScheduler(
    lambda: engine.refresh(trigger=TriggerSource.SCHEDULED),
    interval=300,
    run_immediately=True,
)

async with engine, scheduler:
    await shutdown_requested.wait()
```

- Runs follow a fixed grid of `interval` seconds measured from `start()`. A run that
  takes longer than the interval skips the missed slots instead of running back to
  back, and runs never overlap.
- `pause()` skips scheduled runs until `resume()`; a run that is already active
  finishes.
- `stop()` waits until the scheduler has stopped, including an active run, and
  `stop(cancel=True)` cancels the active run. Stopping is idempotent and safe to
  call concurrently. Called from the scheduled callback itself, `stop()` returns
  without waiting. From code that a scheduled run may wait for, such as an operation
  of the engine that the scheduler refreshes, call `stop(wait=False)`; the scheduler
  then stops as soon as the active run ends.
- When the callback raises, the exception is logged to the `refresh_engine` logger
  and published as a `SCHEDULER_RUN_FAILED` event, and the schedule continues.
- Used with `async with`, the scheduler starts on entry and stops on exit,
  cancelling the active run if the block raised.

![Scheduler states](../assets/diagrams/scheduler-states-light.png#only-light)
![Scheduler states](../assets/diagrams/scheduler-states-dark.png#only-dark)

## External schedulers

When refreshes are triggered by something outside the event loop, such as cron
integration, a job framework, or a thread-based scheduler, use
[`ExternalSchedulerAdapter`][refresh_engine.ExternalSchedulerAdapter]. Create it on
the engine's event loop; it uses the request trigger `EXTERNAL` by default:

```python
from refresh_engine import ExternalSchedulerAdapter

adapter = ExternalSchedulerAdapter(engine.submit)

# On the event loop:
result = await adapter.run()

# From any other thread:
result = adapter.run_threadsafe(timeout=600)
future = adapter.submit_threadsafe()
```

`run_threadsafe()` refuses to run on the event loop's own thread, because it would
block the loop it waits for. When its timeout expires, the caller's wait is
cancelled.

## Overlapping requests

An engine runs one refresh at a time. [`OverlapPolicy`][refresh_engine.OverlapPolicy]
decides what happens to requests that arrive meanwhile:

| Policy | A new request while a refresh is running |
| --- | --- |
| `COALESCE` (default) | Merges into a queued request that covers it, otherwise waits in the queue. Callers of a merged request share one result. |
| `QUEUE` | Waits in the queue and runs on its own, in order. |
| `SKIP_IF_RUNNING` | Returns immediately with status `SKIPPED`. |
| `CANCEL_PREVIOUS` | Cancels the running and queued refreshes, whose callers receive status `CANCELLED`, and runs next. |

Coalescing never loses intent: a request merges only into one whose work includes
it. `FULL` covers every mode, `DEPENDENCY_AWARE` covers `INCREMENTAL`, and two
targeted requests merge into one that targets the union of their IDs and selectors.
An incremental and a targeted request therefore run separately.

```python
from refresh_engine import OverlapPolicy, RefreshConfig, RefreshEngine

engine = RefreshEngine(
    source, operation, config=RefreshConfig(overlap_policy=OverlapPolicy.QUEUE)
)
```

## Cancellation and shutdown

- Cancelling the task that awaits `refresh()` cancels the refresh once no other
  caller is waiting for it, and raises `asyncio.CancelledError` in that task as
  usual. Nothing is committed for a cancelled refresh.
- `close()` stops accepting requests, waits for running and queued refreshes, and
  delivers pending events. `close(cancel=True)` cancels them instead; their callers
  receive results with status `CANCELLED`. `close(wait=False)` returns as soon as
  the engine stops accepting requests.
- Requests submitted after `close()` raise
  [`EngineClosedError`][refresh_engine.EngineClosedError].
- Leaving `async with RefreshEngine(...)` closes the engine, and cancels active work
  if the block raised.

Stop the scheduler before closing the engine it drives; `async with engine,
scheduler:` does this automatically because the scheduler exits first.

## Calling the engine from its own refreshes

The source, operation, fingerprint strategy, and state store of a refresh are part
of that refresh, which cannot finish until they return. Code that the refresh waits
for must therefore never wait for the refresh:

- `refresh()` and `submit()` awaited directly by these components raise
  `RuntimeError` instead of waiting for themselves. To request a follow-up refresh,
  start it in a separate task that the refresh does not wait for, for example with
  `asyncio.create_task(engine.refresh(...))`, or request it after the refresh
  returns.
- `close()` awaited directly by these components returns without waiting.
- The engine cannot detect waits routed through another task, for example with
  `asyncio.gather` or by awaiting a task that calls the engine. Such waits never
  finish. Use `close(wait=False)` and the scheduler's `stop(wait=False)` from any
  code that a refresh may wait for.

Event handlers run outside refreshes, so they may await `refresh()` and `close()`
directly; see [Events, logging, and metrics](observability.md).

## Examples

```python
--8<--
examples/05_scheduling.py
--8<--
```

```text
--8<--
examples/output/05_scheduling.txt
--8<--
```

```python
--8<--
examples/07_overlap_and_cancellation.py
--8<--
```

```text
--8<--
examples/output/07_overlap_and_cancellation.txt
--8<--
```
