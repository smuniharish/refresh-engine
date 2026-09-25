# Failure handling and safety

Source: `src/refresh_engine/api/engine.py`, `src/refresh_engine/detection/detector.py`,
`docs/guides/state-and-failure-safety.md`,
`examples/06_resilience_and_partial_discovery.py`,
`examples/11_cancellation_and_shutdown.py` (verified output below).

## Discovery failure is an error, not "zero resources"

If `ResourceSource.discover()` (or consuming its `DiscoveryResult.resources`
iterator) raises, the engine wraps it as `DiscoveryError` and the whole
refresh fails (`RefreshStatus.FAILED`) with that error in `RefreshResult.errors`.
**It never falls through to "discovered zero resources," which would look
identical to mass deletion.** A duplicate resource ID within one discovery
also raises `DiscoveryError`.

## Partial/incomplete discovery never implies deletion

`DiscoveryResult(complete=False)` is a first-class signal, not an error. When
`complete=False`:

- Resources that *are* yielded are processed normally (can still be `ADDED`/
  `MODIFIED`/`UNCHANGED`).
- Resource IDs present in the previous state but **absent** from this
  discovery are **not** inferred as `DELETED` — `ChangeDetector.detect()`
  explicitly skips deletion inference and appends a diagnostic instead
  ("deletion inference suppressed because discovery was incomplete").
- Only a `complete=True` discovery may plan `PlanAction.DELETE` for IDs that
  disappeared.

### Verified example output

`examples/06_resilience_and_partial_discovery.py`, after marking the source
`complete=False` and removing `"beta"` from it:

```text
partial discovery deletions=0
```

`beta`'s prior state is retained; nothing is deleted because the discovery
that omitted it was marked incomplete.

## State never advances on failure

A resource's `ResourceState` is only staged for commit if its
`ResourceExecutionResult.succeeded` is `True`. Failed executions keep the
resource's previous stored state, so the *next* refresh will still see it as
changed (not silently marked "handled").

## `RefreshStatus` semantics

| Status | Meaning |
| --- | --- |
| `SUCCESS` | No errors occurred. |
| `PARTIAL` | Some resources failed but `FailurePolicy` allowed the refresh to continue and produce a result. |
| `FAILED` | The refresh could not produce any usable result (`FAIL_FAST` with a failure, no executions at all, or an exception during the lifecycle itself, e.g. `DiscoveryError`). |
| `CANCELLED` | Propagated `asyncio.CancelledError`; the caller's own `await engine.refresh()` raises `CancelledError` rather than returning a result. |
| `SKIPPED` | `OverlapPolicy.SKIP_IF_RUNNING` rejected the request while another was in flight; `RefreshResult.skipped(reason)`. |

## Cancellation is cooperative and rolls back

```python
refresh_task = asyncio.create_task(engine.refresh())
...
refresh_task.cancel()
try:
    await refresh_task
except asyncio.CancelledError:
    ...
```

Cancellation propagates through discovery, snapshotting, and execution
(workers are explicitly cancelled and awaited with
`return_exceptions=True` so nothing is orphaned). No state transaction is
committed for a cancelled refresh.

### Verified example output

`examples/11_cancellation_and_shutdown.py` — cancel mid-operation, then check
the store:

```text
cancellation: propagated=True, committed_states=0
```

## Operations must be idempotent

Because timeouts and retries can cause an operation to run more than once (or
be cancelled after the external side effect already completed but before
acknowledgement), `RefreshOperation` implementations must be safe to run more
than once for the same input, and must propagate `CancelledError` rather than
swallowing it. Use application-level idempotency keys where the destination
system supports them.

## Shutdown ordering (avoid orphaned work)

```python
try:
    await scheduler.start()
    await shutdown_requested.wait()
finally:
    await scheduler.stop()          # AsyncScheduler.cancel_active_on_stop controls its own work
    await engine.close()            # RefreshConfig.cancel_active_on_shutdown controls coordinated work
```

`RefreshEngine.close()` awaits the coordinator's shutdown (optionally
cancelling the active/queued work per `cancel_active_on_shutdown`) and drains
the event publisher. Always call `close()` once you are done with an engine,
even if you never started a scheduler.
