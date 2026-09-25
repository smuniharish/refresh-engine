# Scheduling

Source: `src/refresh_engine/scheduling/async_scheduler.py`,
`src/refresh_engine/scheduling/external.py`,
`docs/guides/triggers-and-scheduling.md`,
`examples/04_async_and_external_scheduling.py` (verified output below).

## Mental model

```
Application
  |
  +-- Main execution (never blocked by scheduled refresh)
  |
  +-- AsyncScheduler (owned background task on the same event loop)
          |
          +-- interval timer (APScheduler AsyncIOScheduler under the hood)
          +-- on each tick: create a task that calls your callback
                  -> engine.refresh(trigger=TriggerSource.SCHEDULED)
```

`AsyncScheduler` never blocks the caller: `await scheduler.start()` returns as
soon as the schedule is installed, not when the first refresh completes.
Refreshes run as independently created `asyncio.Task`s on the same loop.

## `AsyncScheduler` (asyncio-native, in-process)

```python
from refresh_engine import AsyncScheduler, TriggerSource

scheduler = AsyncScheduler(
    lambda: engine.refresh(trigger=TriggerSource.SCHEDULED),
    interval=60,            # seconds; must be > 0
    run_immediately=True,   # fire once immediately, then every `interval`
)
await scheduler.start()
...
await scheduler.stop()
await engine.close()
```

Lifecycle (`AsyncSchedulerABC`): `start()`, `pause()`, `resume()`, `stop()` —
all `async`. `SchedulerError` is raised for invalid transitions: starting an
already-started scheduler, or pausing/resuming from the wrong state.

- `run_immediately=False` (default) waits a full `interval` before the first
  callback.
- `cancel_active_on_stop=False` (default) lets an in-flight callback finish
  before `stop()` returns; set `True` to cancel it instead. If you enable
  cancellation, your `RefreshOperation` and `ResourceSource` must remain safe
  under `CancelledError` (see [failure-handling.md](failure-handling.md)).
- `stop()` shuts down the underlying APScheduler timer, wakes the internal
  loop, optionally cancels the active callback, and awaits the scheduler's own
  task — it is safe to call even if `start()` was never called (no-op).

## `ExternalSchedulerAdapter` (another system owns timing)

```python
from refresh_engine import ExternalSchedulerAdapter, RefreshRequest, TriggerSource

adapter = ExternalSchedulerAdapter(engine.submit)
result = await adapter.run(RefreshRequest(trigger=TriggerSource.EXTERNAL))
```

Use this when cron, a Kubernetes `CronJob`, Celery beat, or another
application-level scheduler already owns timing — the adapter is just a thin
`async run(request) -> RefreshResult` wrapper around `engine.submit`.

## Verified example output

`examples/04_async_and_external_scheduling.py`:

```text
external refresh: status=success
```

The async scheduler in that example runs immediately (`run_immediately=True`,
`interval=60`), performs one scheduled refresh, and is stopped before the
external adapter's own refresh runs.

## Triggers -> `RefreshRequest`

Every integration path normalizes to a `RefreshRequest`:

- `engine.refresh(...)` / `engine.submit(request)` — manual.
- `EventTrigger(mapper).request_for(event)` — map an application event object
  to a `RefreshRequest` (or `None` to ignore it).
- `QueryTrigger().request(resource_ids, metadata=None)` — build a `TARGETED`
  request for specific IDs (e.g. from a webhook payload).
- `AsyncScheduler` — periodic, in-process.
- `ExternalSchedulerAdapter` — periodic, externally owned.

```python
from refresh_engine import EventTrigger, QueryTrigger, TriggerSource, RefreshRequest

event_trigger = EventTrigger(
    lambda event: RefreshRequest(trigger=TriggerSource.EVENT) if event == "changed" else None
)
request = event_trigger.request_for("changed")
if request is not None:
    await engine.submit(request)

query_request = QueryTrigger().request(["beta"], metadata={"caller": "webhook"})
await engine.submit(query_request)
```

## Shutdown ordering

Stop trigger intake before closing the engine, and stop a scheduler before
closing its engine:

```python
try:
    await scheduler.start()
    await shutdown_requested.wait()
finally:
    await scheduler.stop()
    await engine.close()
```

## Common mistakes

- Calling `await engine.refresh()` directly inside a request handler on a
  fixed timer using `time.sleep` — this blocks the event loop; use
  `AsyncScheduler` (or an external scheduler) instead.
- Never calling `scheduler.stop()` / `engine.close()` on shutdown, leaking the
  background task and its owned resources.
- Assuming `scheduler.start()` awaits the first refresh — it does not; it only
  installs the schedule.
