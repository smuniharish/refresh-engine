# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-05

This release reworks the engine for correctness, speed, and a smaller, clearer API.
It contains breaking changes; read the [upgrade notes](#upgrading-from-010) first.

### Added

- Support for Python 3.13 and 3.14.
- Selector-based targeted refreshes: `refresh(selector=...)` and
  `RefreshRequest.selector`. Selectors compose with `&`, `|`, and `~`, and
  `TagSelector` reads tags from a configurable metadata `key`.
- `MetricsEventHandler`, which turns lifecycle events into counters and observations
  on any `MetricsSink`.
- `ExternalSchedulerAdapter.submit_threadsafe()` and `run_threadsafe()` for
  schedulers that run outside the event loop.
- `RefreshEngine` and `AsyncScheduler` are asynchronous context managers, and
  `RefreshEngine.closed` reports whether the engine accepts requests.
- The source, operation, fingerprint strategy, and state store of a refresh run in
  the context of the code that requested it, so context variables such as tracing
  spans reach them. Event handlers run in a fresh context.
- Protection against waiting for oneself: `RefreshEngine.close(wait=False)` and
  `AsyncScheduler.stop(wait=False)` shut down without waiting, for code that a
  refresh or scheduled run waits for. Awaited directly by the engine's own source,
  operation, fingerprint strategy, or state store, `refresh()` and `submit()` raise
  `RuntimeError` and `close()` returns without waiting. Awaited directly by a
  handler, `EventPublisher.drain()` returns immediately.
- `canonical_digest()` for writing fingerprint strategies. Fingerprints now support
  `bytearray`, `memoryview`, `UUID`, `Decimal`, `time`, `timedelta`, and enumerations
  with any supported value.
- `StateTransactionABC` stages changes and passes them to a single abstract method,
  `apply_changes()`, which makes durable stores much shorter to write.
- Automatic retries: an unchanged resource whose planned refresh did not complete,
  such as a dependent skipped after a failure, is retried by the next refresh in any
  mode.
- Dependency cycles fail only the resources in the cycle, each with the cycle it
  belongs to, and skip their dependents; everything else is refreshed.
- `RefreshPhase`, `RefreshResult.request`, `RefreshResult.dependency_changed_count`,
  `RefreshEvent.correlation_id`, and `RefreshEvent.error`.
- Event kinds `SNAPSHOT_STARTED`, `SNAPSHOT_COMPLETED`, `RESOURCE_SNAPSHOT_FAILED`,
  `RESOURCE_REFRESH_SKIPPED`, `REFRESH_SKIPPED`, and `SCHEDULER_RUN_FAILED`, and
  richer attributes on every event, including the request metadata on
  `REFRESH_STARTED`.
- `EngineClosedError`, and `SnapshotError` for snapshots that violate the source
  contract.
- `DiscoveryResult` accepts synchronous iterables as well as asynchronous ones.

### Changed

- **Breaking:** the supported Python versions are 3.12 and newer, and the runtime
  dependencies are `structlog>=26.1.0` and `tenacity>=9.1.4`. APScheduler and NetworkX
  are no longer required.
- **Breaking:** every public name is imported from `refresh_engine`. The
  `api`, `core`, `detection`, `events`, `execution`, `observability`, `planning`,
  `scheduling`, `stores`, and `triggers` subpackages were replaced.
- **Breaking:** the fingerprint encoding is new, so the first refresh after
  upgrading reports every resource as modified and refreshes it once.
  `CompositeHash` no longer fingerprints dependencies; dependency changes are
  reported as `DEPENDENCY_CHANGED`. `VersionTokenFingerprint` and `ETagFingerprint`
  use the token itself as the fingerprint value.
- **Breaking:** operations always receive a `Resource`, including for deletions,
  and protocol parameters are positional-only.
- **Breaking:** `EventPublisher.publish()` is synchronous and never blocks.
  Synchronous handlers run on the event loop, and coroutine handlers are awaited.
  `EventPublisherABC` gains `has_subscribers`.
- **Breaking:** `StructlogEventHandler` logs the event kind as the event name, uses
  per-kind log levels, adds `occurred_at`, omits empty IDs, and passes failures as
  `exc_info`. `default_redactor` matches credential-like keys in any naming style
  and at any nesting depth.
- **Breaking:** `TimeoutConfig.resource_refresh` is renamed to `operation`.
- **Breaking:** `RefreshConfig.cancel_active_on_shutdown` is replaced by
  `RefreshEngine.close(cancel=...)`, and `AsyncScheduler(cancel_active_on_stop=...)`
  by `AsyncScheduler.stop(cancel=...)`.
- **Breaking:** model fields after the identifier are keyword-only.
  `Resource.dependencies` and `ResourceSnapshot.dependencies` default to `None`,
  meaning "not declared". Invalid arguments raise `ConfigurationError`, a
  `ValueError`.
- **Breaking:** overlapping requests are coalesced only when one request covers the
  other, so incremental and targeted requests run separately. Requests superseded
  under `CANCEL_PREVIOUS`, or cancelled by `close(cancel=True)`, now return a
  `CANCELLED` result instead of raising `asyncio.CancelledError`.
- **Breaking:** statuses follow precise rules: `PARTIAL` when some but not all
  in-scope resources failed, `FAILED` when the refresh could not complete, the
  fail-fast policy stopped it, or everything failed. `RefreshResult.failed_count` is
  a property equal to `len(errors)`.
- **Breaking:** an overall timeout returns a `FAILED` result instead of raising
  `TimeoutError`, and the best-effort policy still commits completed work.
- **Breaking:** `ExternalSchedulerAdapter` is created with `submit` and an optional
  keyword-only `loop`.
- Targeted refreshes observe only the targeted resources.
- `AsyncScheduler` runs on a fixed interval grid, skips missed slots, never overlaps
  runs, and keeps running after a callback fails.

### Removed

- The pipeline internals `ChangeDetector`, `RefreshPlanner`, `ImpactAnalyzer`,
  `DependencyGraph`, `Change`, `ChangeSet`, `ChangeType`, and `ImpactSet`.
- `EventTrigger` and `QueryTrigger`; create a `RefreshRequest` or call `refresh()`
  directly.
- `AsyncSchedulerABC`.
- The exceptions `ChangeDetectionError`, `PlanningError`, `ExecutionError`,
  `CancellationError`, and `DependencyError`, which were never raised.
- `FailurePolicy.RETRY_THEN_CONTINUE`, which behaved exactly like `BEST_EFFORT`;
  configure retries with `RetryConfig`.
- `RefreshRequest.priority`, `RefreshRequest.deduplication_key`,
  `RefreshRequest.merge()`, `Resource.source`, `RefreshResult.skipped()`, and
  `canonical_bytes()`.

### Fixed

- Dependencies declared only by snapshots were dropped after a cheap-indicator
  match, which caused a needless refresh and lost the dependency.
- A dependency-aware refresh after an incomplete discovery called operations
  without a resource for undiscovered dependents.
- A failed snapshot of an impacted resource failed the whole refresh and left tasks
  running.
- A failing scheduler callback stopped the schedule permanently and was re-raised
  by `stop()`, and scheduler wake-ups ran on a worker thread.
- A refresh whose only change failed reported `PARTIAL`, while a single snapshot
  failure among unchanged resources reported `FAILED`; failed snapshots were counted
  as unchanged.
- `CANCEL_PREVIOUS` raised `asyncio.CancelledError` in callers that were not
  cancelled.
- A failed commit discarded the execution results.
- `FAIL_FAST` kept running the operations of the current level after a failure, and
  committed state after snapshot failures.
- Dependents of a failed operation ran against stale input.
- Every unchanged resource was written to the state store on every refresh.
- A custom store, fingerprint strategy, or event publisher that evaluated as false
  was replaced with the default.
- Fingerprinting raised `TypeError` for enumerations with non-JSON values, hashed
  `bytearray` values element by element, and ignored dataclass types.
- `TagSelector` split a string tag into characters.
- `StructlogEventHandler` failed for an event attribute named `event`.
- A queued request whose callers had all cancelled still ran, and the publisher's
  error list grew without bound.
- Lifecycle events did not carry the request's correlation ID.

### Performance

- A full refresh of 100,000 resources takes 3.1 s instead of 9.5 s and peaks at
  114 MB instead of 513 MB. Incremental refreshes are about twice as fast.
- Events are not built when nobody subscribes, and the publisher delivers them from
  one background task instead of one task and one thread hop per event.
- Unchanged resources are no longer written to the state store, and only snapshots
  of resources that will receive an operation stay in memory.

### Upgrading from 0.1.0

1. Import every name from `refresh_engine`.
2. Update operations to the positional signature
   `(resource, snapshot, action, request, /)`; `resource` is never `None`.
3. Replace `EventTrigger` and `QueryTrigger` with `RefreshRequest` or `refresh()`,
   and `FailurePolicy.RETRY_THEN_CONTINUE` with `BEST_EFFORT` plus a `RetryConfig`.
4. Rename `TimeoutConfig(resource_refresh=...)` to `TimeoutConfig(operation=...)`,
   and move shutdown cancellation to `close(cancel=True)` and `stop(cancel=True)`.
5. Pass model fields after the identifier by keyword, for example
   `Resource("a", metadata={...})`.
6. Port custom state transactions to `StateTransactionABC.apply_changes()`, or keep
   implementing the `StateTransaction` protocol.
7. Expect the first refresh after the upgrade to refresh every resource once.

## [0.1.0] - 2026-09-24

### Added

- Initial release: asyncio-first discovery, fingerprinting, change detection,
  dependency-aware planning, bounded execution with retries and timeouts,
  transactional state, lifecycle events, structured logging, and scheduling.

[Unreleased]: https://github.com/smuniharish/refresh-engine/compare/v0.2.0...HEAD
[0.2.0]: https://pypi.org/project/refresh-engine/0.2.0/
[0.1.0]: https://pypi.org/project/refresh-engine/0.1.0/
