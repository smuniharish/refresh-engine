---
name: refresh-engine
description: Integrate, configure, test, and troubleshoot refresh-engine, the asyncio Python library (imported as refresh_engine) that keeps derived data such as search indexes, caches, embeddings, configuration, and registries in sync with a source by refreshing only the resources that were added, modified, deleted, or affected by a dependency change. Use when writing a resource source or refresh operation; choosing refresh modes, fingerprints, or cheap change indicators; declaring dependencies between resources; implementing a durable state store; scheduling periodic or event-driven refreshes; configuring retries, timeouts, failure policies, or overlap policies; adding structured logging or metrics; or debugging why resources were or were not refreshed or deleted.
license: Apache-2.0
metadata:
  author: S. Muni Harish
  version: "0.2.0"
  documentation: https://refresh-engine.readthedocs.io
---

# refresh-engine

refresh-engine keeps a destination in sync with a collection of source resources.
The application supplies two small pieces:

- a **source** that lists resources (`discover`) and reads one resource
  (`snapshot`);
- an **operation** that applies one change to the destination.

The engine does the rest. It fingerprints snapshots and compares them with the
state of the previous refresh. It plans refreshes and deletions in dependency
order and runs them with bounded concurrency, retries, and timeouts. Finally it
commits the new state of every resource that succeeded.

## When to use it

Use refresh-engine when:

- a collection of resources must be mirrored, indexed, or transformed, and only a
  small part of it changes between runs;
- changes must propagate to resources that depend on the changed ones;
- refreshes must run periodically or on demand without blocking the application;
- existing code diffs, hashes, orders, or schedules this work by hand.

Do not use it to re-fetch a single value, for request caching with expiry times,
or as a general-purpose job scheduler.

## Workflow

1. **Check the version.** This skill describes refresh-engine 0.2, which requires
   Python 3.12 or newer. Run
   `python -c "import refresh_engine; print(refresh_engine.__version__)"`, or
   install it with `pip install refresh-engine`. Projects on 0.1 must follow the
   upgrade notes in the
   [changelog](https://refresh-engine.readthedocs.io/en/latest/changelog/) first.
2. **Model the resources.** Give each resource a stable, unique string ID. Decide
   what discovery can list cheaply, such as IDs, tags, versions, or ETags, and what
   a snapshot must read, such as the content.
3. **Implement the source and the operation**:
   [references/sources-and-operations.md](references/sources-and-operations.md).
4. **Choose change detection**, the fingerprint strategy and an optional cheap
   indicator: [references/change-detection.md](references/change-detection.md).
5. **Declare dependencies** when a resource's output depends on other resources:
   [references/dependencies.md](references/dependencies.md).
6. **Choose a state store.** Production integrations need a durable one:
   [references/state-stores.md](references/state-stores.md).
7. **Configure failures and triggers**: retries, timeouts, policies, modes, and
   scheduling, in [references/failure-handling.md](references/failure-handling.md),
   [references/refresh-modes.md](references/refresh-modes.md), and
   [references/scheduling.md](references/scheduling.md).
8. **Add logging and metrics**:
   [references/observability.md](references/observability.md).
9. **Test** the integration with in-memory fakes:
   [references/testing.md](references/testing.md).

Start new integrations from [assets/integration_template.py](assets/integration_template.py).
When something behaves unexpectedly, use
[references/troubleshooting.md](references/troubleshooting.md).

## Rules

Each rule prevents lost data, missed changes, or refreshes that never converge.

1. **Raise when discovery fails.** Never return an empty or partial listing as
   complete, because a complete discovery that omits a resource deletes it. Return
   `DiscoveryResult(..., complete=False)` when the listing may be partial.
2. **Raise when an operation fails.** Do not catch and log errors inside the
   operation. A raised exception keeps the resource's previous state, so the next
   refresh retries it.
3. **Make operations idempotent**, and never suppress `asyncio.CancelledError`. An
   operation may run again after a retry, a timeout, or a restart.
4. **Handle deletions.** For `PlanAction.DELETE`, `snapshot` is `None` and
   `resource` carries only the ID.
5. **Fingerprint everything the operation uses, and nothing volatile.** Clocks,
   random values, and counters that change without a real change cause needless
   refreshes.
6. **Mark a cheap indicator reliable only if it changes whenever the fingerprinted
   data changes.** Otherwise the change is missed.
7. **Persist state durably in production**, with atomic commits, and run one
   engine per set of stored states.
8. **Check results.** Operational failures are reported in `RefreshResult.status`
   and `RefreshResult.errors`; they are not raised.
9. **Close engines and schedulers**, preferably with `async with`.
10. **Never wait for the engine from its own refresh.** A source, operation, or state
    store that awaits `refresh()` of its own engine gets `RuntimeError`, and waits
    routed through another task never finish. Start follow-up refreshes in a
    separate task that the refresh does not wait for, and shut down from such code
    with `close(wait=False)` or `stop(wait=False)`.
11. **Keep the event loop free.** Run blocking I/O with `asyncio.to_thread`.
12. **Import only from `refresh_engine`.** Every public name is exported there.

## Minimal integration

<!-- test -->
```python
import asyncio

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)

SOURCE = {"a": "alpha", "b": "beta"}
DESTINATION: dict[str, str] = {}


class Source:
    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult([Resource(key) for key in sorted(SOURCE)])

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource.resource_id, content=SOURCE[resource.resource_id]
        )


async def apply(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    if action is PlanAction.DELETE:
        DESTINATION.pop(resource.resource_id, None)
    elif snapshot is not None:
        DESTINATION[resource.resource_id] = snapshot.content.upper()


async def main() -> None:
    async with RefreshEngine(Source(), apply) as engine:
        await engine.refresh()
        SOURCE["a"] = "alpha 2"
        del SOURCE["b"]
        result = await engine.refresh()
        print(result.status, result.modified_count, result.deleted_count, DESTINATION)


asyncio.run(main())
```

```text
success 1 1 {'a': 'ALPHA 2'}
```

## API quick reference

| API | Purpose |
| --- | --- |
| `RefreshEngine(source, operation, *, fingerprint=None, store=None, config=None, events=None)` | The engine; defaults to `CompositeHash`, `InMemoryStateStore`, `RefreshConfig()`, and a new `EventPublisher`. |
| `await engine.refresh(*, mode=None, resource_ids=(), selector=None, trigger=TriggerSource.MANUAL, metadata=None, correlation_id=None)` | Run a refresh and return a `RefreshResult`. The mode defaults to `TARGETED` when IDs or a selector are given, otherwise `INCREMENTAL`. |
| `await engine.submit(request)` | Run a prepared `RefreshRequest`. |
| `await engine.close(cancel=False, wait=True)` | Stop accepting requests, then wait for or cancel the accepted ones; `wait=False` returns at once. |
| `Resource(resource_id, *, metadata, version, dependencies, cheap_indicator, cheap_indicator_reliable)` | One discovered resource. |
| `ResourceSnapshot(resource_id, *, content, metadata, version, version_token, etag, dependencies)` | The observable state of one resource. |
| `DiscoveryResult(resources, *, complete=True, diagnostics=())` | The result of `discover()`; `resources` may be sync or async iterable. |
| `RefreshConfig(*, max_concurrency=10, overlap_policy, failure_policy, retry, timeout)` | Engine settings. |
| `RetryConfig(*, max_attempts=1, initial_delay=0.1, max_delay=5.0, exponential_base=2.0, jitter=0.0, retryable_exceptions=(Exception,))` | Operation retries. |
| `TimeoutConfig(*, discovery=None, snapshot=None, operation=None, overall=None)` | Timeouts in seconds. |
| `CompositeHash`, `ContentHash`, `MetadataHash`, `VersionTokenFingerprint`, `ETagFingerprint`, `canonical_digest` | Fingerprinting. |
| `IDSelector`, `TagSelector(tags, *, match_all=True, key="tags")`, `PredicateSelector` | Targeting; combine with `&`, `\|`, and `~`. |
| `InMemoryStateStore`, `StateStoreABC`, `StateTransactionABC` | State persistence. |
| `AsyncScheduler(callback, interval, *, events=None, run_immediately=False)`, `ExternalSchedulerAdapter(submit, *, loop=None)` | Scheduling. |
| `EventPublisher`, `StructlogEventHandler`, `MetricsEventHandler`, `InMemoryMetrics`, `default_redactor` | Observability. |

Enumerations: `RefreshMode` (`INCREMENTAL`, `FULL`, `TARGETED`, `DEPENDENCY_AWARE`),
`RefreshStatus` (`SUCCESS`, `PARTIAL`, `FAILED`, `CANCELLED`, `SKIPPED`),
`FailurePolicy` (`BEST_EFFORT`, `FAIL_FAST`), `OverlapPolicy` (`COALESCE`, `QUEUE`,
`SKIP_IF_RUNNING`, `CANCEL_PREVIOUS`), `PlanAction` (`REFRESH`, `DELETE`),
`TriggerSource` (`MANUAL`, `SCHEDULED`, `EVENT`, `QUERY`, `EXTERNAL`), and
`RefreshPhase`.

`RefreshResult` reports `status`, `discovered_count`, `added_count`,
`modified_count`, `dependency_changed_count`, `unchanged_count`, `deleted_count`,
`impacted_count`, `refreshed_count`, `skipped_count`, `failed_count`, `errors`,
`warnings`, `executions`, `duration`, and the `request` that ran.

`refresh()` and `submit()` raise only for programming errors: `ConfigurationError`
(also a `ValueError`) for inconsistent arguments, `TypeError` for an object that is
not a `RefreshRequest`, `EngineClosedError` after `close()`, and `RuntimeError` when
awaited directly by the engine's own source, operation, fingerprint strategy, or
state store. Cancelling the task that awaits them raises `asyncio.CancelledError` as
usual. Constructors raise `ConfigurationError` for invalid values and `TypeError`
for objects that do not implement a contract.

## References

| Reference | Read it when |
| --- | --- |
| [sources-and-operations.md](references/sources-and-operations.md) | Writing or reviewing a source or an operation. |
| [refresh-modes.md](references/refresh-modes.md) | Choosing a mode, targeting resources, or building requests. |
| [change-detection.md](references/change-detection.md) | Choosing or writing a fingerprint strategy, or adding cheap indicators. |
| [dependencies.md](references/dependencies.md) | Resources depend on other resources. |
| [failure-handling.md](references/failure-handling.md) | Configuring retries, timeouts, or failure policies, or reading results. |
| [state-stores.md](references/state-stores.md) | Persisting state in a database. |
| [scheduling.md](references/scheduling.md) | Running refreshes periodically, from other threads, or concurrently, and shutting down. |
| [observability.md](references/observability.md) | Adding events, logging, or metrics. |
| [testing.md](references/testing.md) | Testing an integration. |
| [troubleshooting.md](references/troubleshooting.md) | Diagnosing unexpected refreshes, deletions, statuses, or errors. |

The full documentation is at <https://refresh-engine.readthedocs.io>.
