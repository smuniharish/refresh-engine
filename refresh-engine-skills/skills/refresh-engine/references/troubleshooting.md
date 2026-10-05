# Troubleshooting

Start with the `RefreshResult`: `status`, `errors` with their `phase`, `warnings`,
and the counts. Then match the symptom below.

## Too much work

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Every resource is refreshed on every run. | State is lost between runs: `InMemoryStateStore` in a process that restarts, or a new store per engine. | Use a durable store, and reuse one store instance. |
| | The fingerprint covers volatile data, such as timestamps or view counts. | Use a narrower strategy, or a custom one that hashes only what the operation uses. |
| | The store does not return the stored fingerprints. | Make `load_all()` return every field exactly as written. |
| Every resource is refreshed once after an upgrade or a configuration change. | The fingerprint strategy or encoding changed. | Expected; it happens once. |
| Unchanged resources are written to the store on every run. | The store changes metadata types, for example tuples into lists, or discovery reports a changing version or cheap indicator. | Round-trip values exactly, and keep versions and indicators stable. |
| Snapshots are read for unchanged resources. | No cheap indicator, `cheap_indicator_reliable=False`, or a `FULL` or `TARGETED` refresh. | Add a reliable cheap indicator, and use `INCREMENTAL` for routine runs. |

## Missed changes

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| A change is not detected. | A reliable cheap indicator did not change. | Use an indicator that changes with every edit, or mark it unreliable. |
| | The fingerprint strategy ignores the changed field, for example `ContentHash` with a metadata change. | Use `CompositeHash` or a strategy that covers the field. |
| Dependents are not refreshed when a dependency changes. | `INCREMENTAL` refreshes only changed resources, or dependencies are not declared. | Use `RefreshMode.DEPENDENCY_AWARE`, and declare dependencies. |
| A targeted refresh ignores an ID. | The ID was not discovered; see `warnings`. | Check the ID and the discovery. |

## Deletions

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Deleted resources stay in the destination. | Discovery returned `complete=False`; `warnings` mentions suppressed deletions. | Return a complete listing when it is complete. |
| | Discovery failed, so the refresh stopped; `errors` has a `discovery` issue. | Fix the source. |
| | The delete operation failed; `errors` has an `execution` issue. | Fix the operation; the deletion is retried. |
| Resources were deleted unexpectedly. | Discovery returned an empty or partial listing as complete during an outage. | Raise from `discover()` on failure, and set `complete=False` for partial listings. |

## Statuses and errors

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `PARTIAL` | Some resources failed. | Inspect `errors`; failed resources are retried automatically. |
| `FAILED` with a `persistence` issue | The state store failed to load or commit. | Check the database; nothing was committed. |
| `FAILED` with a refresh-level timeout | `TimeoutConfig.overall` expired. | Raise the timeout, or reduce the work per refresh; best-effort progress was committed. |
| `FAILED` under `FAIL_FAST` | A resource failed, and nothing was committed. | Fix the failure, or use `BEST_EFFORT`. |
| A `planning` issue naming a cycle | A dependency cycle. | Break the cycle in the source data. |
| `not run because dependency ... failed` | An upstream operation failed. | Fix the upstream resource; dependents retry automatically. |
| `SKIPPED` | `SKIP_IF_RUNNING` while another refresh ran. | Expected; choose another overlap policy if needed. |
| `CANCELLED` | Superseded under `CANCEL_PREVIOUS`, or `close(cancel=True)`. | Expected. |
| `EngineClosedError` | A request after `close()`. | Keep the engine open for the application's lifetime. |
| `RuntimeError`: the refresh would wait for itself | A source, operation, fingerprint strategy, or state store awaited `refresh()` or `submit()` of its own engine. | Start the follow-up refresh in a separate task, or request it after the refresh returns. |
| A refresh, `close()`, or `stop()` never finishes | Code that the refresh or scheduled run waits for waits for it in turn, for example through `asyncio.gather` or by awaiting `stop()` from an operation. | Shut down from such code with `close(wait=False)` or `stop(wait=False)`, and never await follow-up refreshes there. |
| `ConfigurationError` | Inconsistent arguments, such as `resource_ids` with a mode other than `TARGETED`. | Fix the call; the message names the argument. |
| `TypeError` when creating the engine | The source, operation, or store does not implement its contract. | Implement the methods with the documented signatures, or subclass the ABCs. |

## Responsiveness

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| The application stalls during refreshes. | Blocking I/O in the source, operation, store, or a synchronous event handler. | Use async clients or `asyncio.to_thread`, and keep handlers fast. |
| Scheduled refreshes run late or are skipped. | Refreshes take longer than the interval, so slots are skipped by design. | Increase the interval, or reduce the work per refresh. |
| `run_threadsafe()` raises `RuntimeError`. | It was called on the event loop's thread. | Use `await adapter.run()` on the loop. |
| No logs appear. | No `StructlogEventHandler` is subscribed, the engine uses another publisher, or structlog filters the level. | Subscribe the handler to the publisher passed to the engine, and check the structlog configuration. |
