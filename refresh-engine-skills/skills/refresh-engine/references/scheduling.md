# Scheduling

Source: `src/refresh_engine/scheduling/async_scheduler.py`,
`src/refresh_engine/scheduling/external.py`,
`docs/guides/triggers-and-scheduling.md`.

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
  under `CancelledError` (see [state-management.md](state-management.md)).
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

## Verified against

[`examples/04_async_and_external_scheduling.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/04_async_and_external_scheduling.py)
runs an `AsyncScheduler` immediately (`run_immediately=True`, `interval=60`)
for one scheduled refresh, stops it, then performs a second refresh through
`ExternalSchedulerAdapter`. Run the script directly to see both refreshes
complete without blocking the surrounding `asyncio` program.

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

## Concurrency, overlap, retries, and timeouts

Source: `src/refresh_engine/core/models.py` (`RefreshConfig`, `RetryConfig`,
`TimeoutConfig`), `src/refresh_engine/execution/coordinator.py`,
`src/refresh_engine/execution/executor.py`.

### `RefreshConfig`

```python
from refresh_engine import RefreshConfig, OverlapPolicy, FailurePolicy, RetryConfig, TimeoutConfig

config = RefreshConfig(
    max_concurrency=10,                      # bounded concurrent resource operations
    overlap_policy=OverlapPolicy.COALESCE,    # default
    failure_policy=FailurePolicy.BEST_EFFORT, # default
    retry=RetryConfig(),                       # max_attempts=1 by default (no retry)
    timeout=TimeoutConfig(),                   # all None by default (no timeouts)
    cancel_active_on_shutdown=False,
)
```

`max_concurrency` must be `>= 1`; timeouts must be `> 0` if set — both raise
`ValueError` in `__post_init__` otherwise.

### Bounded concurrency

`RefreshExecutor.execute()` creates an `asyncio.Semaphore(max_concurrency)` and
runs each dependency level's items concurrently through it; snapshot/fingerprint
work during discovery is similarly worker-pooled at
`min(max_concurrency, len(resources))`. Never bypass this by spawning your own
unbounded tasks around the engine — if you need higher throughput, increase
`max_concurrency` deliberately and size it to your downstream system's real
capacity (connection pools, rate limits), not "as high as possible."

### Overlap policy (`OverlapPolicy`, on `RefreshCoordinator`)

| Policy | Behavior when a refresh is requested while one is already running/queued |
| --- | --- |
| `SKIP_IF_RUNNING` | Returns `RefreshResult.skipped("refresh already running")` immediately; the new request does not run at all. |
| `QUEUE` | The new request is queued and runs after the current one finishes, in submission order. |
| `COALESCE` (default) | The new request is merged (`RefreshRequest.merge`) into the already-pending request; both callers' futures resolve to the one merged run's result. |
| `CANCEL_PREVIOUS` | The active run (and any other pending ones) is cancelled; the new request becomes the one that runs. |

`RefreshRequest.merge` widens `mode` to `FULL` if either side is `FULL`, else
to `DEPENDENCY_AWARE` if either side is `DEPENDENCY_AWARE`, unions
`resource_ids`, keeps the max `priority`, merges `metadata`, keeps the earliest
`requested_at`, and prefers the newer `correlation_id`. See
[`examples/08_custom_store_and_overlap.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/08_custom_store_and_overlap.py)
for two concurrent `engine.refresh()` calls resolving to one merged run under
the default `COALESCE` policy.

### Failure policy (`FailurePolicy`)

| Policy | Behavior |
| --- | --- |
| `FAIL_FAST` | Stop executing further plan levels as soon as any resource in a level fails; do not persist any state from that refresh if any resource failed. |
| `BEST_EFFORT` (default) | Keep executing remaining resources/levels; report `RefreshStatus.PARTIAL` if some failed, `SUCCESS` if none did. |
| `RETRY_THEN_CONTINUE` | Retry failed resources per `RetryConfig`, then continue with best-effort semantics for whatever still fails. |

### `RetryConfig`

```python
RetryConfig(
    max_attempts=1,             # 1 = no retry; must be >= 1
    initial_delay=0.1,
    max_delay=5.0,
    exponential_base=2.0,
    jitter=0.0,
    retryable_exceptions=(Exception,),
)
```

Retries are implemented with Tenacity (`AsyncRetrying`, `stop_after_attempt`,
`wait_exponential` + `wait_random` jitter) inside
`RefreshExecutor._execute_item`, scoped to a single resource's operation call —
not to discovery or the whole refresh. See
[`examples/06_resilience_and_partial_discovery.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/06_resilience_and_partial_discovery.py)
for a resource that fails once under `RETRY_THEN_CONTINUE` then succeeds on
retry.

### `TimeoutConfig`

```python
TimeoutConfig(
    discovery=None,         # wraps ResourceSource.discover() consumption
    snapshot=None,          # wraps each ResourceSource.snapshot() call
    resource_refresh=None,  # wraps each RefreshOperation call
    overall=None,           # wraps the entire refresh lifecycle
)
```

All default to `None` (no timeout). Any configured timeout uses
`asyncio.wait_for` and raises the equivalent `TimeoutError`/`CancelledError`
path — treat exceeded timeouts the same as any other failure for that
resource (see [state-management.md](state-management.md)); the corresponding
operation may be retried per `RetryConfig`, so operations must be idempotent.

### Common concurrency mistakes

- Setting `max_concurrency` very high "for speed" without considering the
  downstream system's real capacity.
- Relying on `FAIL_FAST` in a large batch when a single unrelated resource
  failure should not block hundreds of unrelated resources — prefer
  `BEST_EFFORT` or `RETRY_THEN_CONTINUE` for large heterogeneous resource
  sets.
- Configuring retries with a non-idempotent operation — a partially applied
  side effect can then be applied twice.
- Assuming `COALESCE` guarantees the merged request preserves your exact
  original `resource_ids`/`mode` — it widens per `RefreshRequest.merge`
  semantics above.
