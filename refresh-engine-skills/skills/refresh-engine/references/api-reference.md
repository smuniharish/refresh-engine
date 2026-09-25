# API reference (verified)

This reference lists only symbols present in the installed `refresh_engine`
package (`refresh_engine.__all__`, `refresh-engine==0.1.0`). Every signature below
was read directly from `src/refresh_engine/`. If you need a symbol not listed
here, check `refresh_engine.__all__` in the installed environment before using
it — do not assume it exists.

## Engine

### `RefreshEngine(source, operation, *, fingerprint=None, store=None, config=None, events=None)`

`src/refresh_engine/api/engine.py`

- `source: ResourceSource`, `operation: RefreshOperation` — required.
- `fingerprint: FingerprintStrategy | None` — defaults to `CompositeHash()`.
- `store: StateStore | None` — defaults to `InMemoryStateStore()`.
- `config: RefreshConfig | None` — defaults to `RefreshConfig()`.
- `events: EventPublisher | None` — defaults to a new `EventPublisher()`.

Methods:

- `async refresh(*, mode=RefreshMode.INCREMENTAL, resource_ids=frozenset(), trigger=TriggerSource.MANUAL, metadata=None, correlation_id=None) -> RefreshResult`
- `async submit(request: RefreshRequest) -> RefreshResult`
- `async close() -> None` — awaits coordinator shutdown and drains events; call
  once when done with the engine.

## Models (`refresh_engine.core.models`, all re-exported at top level)

- `Resource(resource_id, metadata={}, version=None, dependencies=frozenset(), source=None, cheap_indicator=None, cheap_indicator_reliable=False)` — frozen dataclass; `resource_id` must be non-empty.
- `ResourceSnapshot(resource_id, content=None, metadata={}, version=None, version_token=None, etag=None, dependencies=frozenset())` — frozen dataclass.
- `DiscoveryResult(resources: AsyncIterator[Resource], complete: bool = True, diagnostics: tuple[str, ...] = ())`.
- `ResourceFingerprint(algorithm: str, value: str)`.
- `ResourceState(resource_id, fingerprint, version=None, cheap_indicator=None, snapshot_metadata={}, dependencies=frozenset(), refreshed_at=<utc now>)`.
- `ChangeType` (`StrEnum`): `ADDED`, `MODIFIED`, `DELETED`, `UNCHANGED`, `DEPENDENCY_CHANGED`.
- `Change(resource_id, change_type, previous: ResourceState | None, current: ResourceState | None)`.
- `ChangeSet(changes: tuple[Change, ...], diagnostics=())` — properties `.added`, `.modified`, `.deleted`, `.unchanged`, `.dependency_changed`, `.counts` (mapping of `ChangeType` -> count), `.resource_ids`.
- `ImpactSet(directly_changed, indirectly_impacted, unaffected)` — property `.all_impacted`.
- `RefreshMode` (`StrEnum`): `FULL`, `INCREMENTAL`, `TARGETED`, `DEPENDENCY_AWARE`.
- `TriggerSource` (`StrEnum`): `MANUAL`, `SCHEDULED`, `EVENT`, `QUERY`, `EXTERNAL`.
- `OverlapPolicy` (`StrEnum`): `SKIP_IF_RUNNING`, `QUEUE`, `COALESCE`, `CANCEL_PREVIOUS`.
- `FailurePolicy` (`StrEnum`): `FAIL_FAST`, `BEST_EFFORT`, `RETRY_THEN_CONTINUE`.
- `RefreshStatus` (`StrEnum`): `SUCCESS`, `PARTIAL`, `FAILED`, `CANCELLED`, `SKIPPED`.
- `RefreshRequest(mode=INCREMENTAL, resource_ids=frozenset(), trigger=MANUAL, priority=0, metadata={}, requested_at=<utc now>, correlation_id=None)` — `TARGETED` requires non-empty `resource_ids`; has `.deduplication_key` and `.merge(other)`.
- `PlanAction` (`StrEnum`): `REFRESH`, `DELETE`.
- `RefreshIssue(phase, message, resource_id=None, exception_type=None)`.
- `ResourceExecutionResult(resource_id, action, succeeded, attempts, duration, error=None)`.
- `RefreshResult(refresh_id, status, started_at, completed_at, duration, discovered_count=0, added_count=0, modified_count=0, deleted_count=0, unchanged_count=0, impacted_count=0, refreshed_count=0, skipped_count=0, failed_count=0, errors=(), warnings=(), executions=())` — classmethod `RefreshResult.skipped(reason)`.
- `RetryConfig(max_attempts=1, initial_delay=0.1, max_delay=5.0, exponential_base=2.0, jitter=0.0, retryable_exceptions=(Exception,))`.
- `TimeoutConfig(discovery=None, snapshot=None, resource_refresh=None, overall=None)` — all optional seconds; `None` means no timeout.
- `RefreshConfig(max_concurrency=10, overlap_policy=OverlapPolicy.COALESCE, failure_policy=FailurePolicy.BEST_EFFORT, retry=RetryConfig(), timeout=TimeoutConfig(), cancel_active_on_shutdown=False)`.

## Fingerprint strategies (`refresh_engine.detection`)

- `ChangeDetector` — `detect(previous, current, *, discovery_complete, diagnostics=()) -> ChangeSet`.
- `CompositeHash(*, include_dependencies=True)` — default; hashes content, metadata, version, version_token, etag, (optionally) sorted dependencies.
- `ContentHash` — hashes `snapshot.content` only.
- `MetadataHash` — hashes `snapshot.metadata` only.
- `VersionTokenFingerprint` — hashes `snapshot.version_token`; raises `FingerprintError` if it is `None`.
- `ETagFingerprint` — hashes `snapshot.etag`; raises `FingerprintError` if it is `None`.

All fingerprints are `sha256:<namespace>` + hex digest over a canonical JSON
encoding. Supported types: `None`, `bool`, `int`, `float` (finite only), `str`,
`bytes`, `datetime`/`date`, `PurePath`, `Enum`, dataclasses, `Mapping`, `Set`,
`Sequence`. Anything else raises `FingerprintError`.

## Planning (`refresh_engine.planning`)

- `DependencyGraph()` — `add_resource(id)`, `add_dependency(resource_id, dependency_id)`, `remove_dependency(...)`, `dependencies_of(id)`, `dependents_of(id)`, `transitive_dependents(ids)`, `topological_levels(ids=None) -> tuple[tuple[str, ...], ...]` (raises `DependencyCycleError` on a cycle).
- `ImpactAnalyzer` — `analyze(changes: ChangeSet, graph: DependencyGraph) -> ImpactSet`.
- `RefreshPlanner` — `plan(refresh_id, request, changes, impact, graph, current) -> RefreshPlan` (`RefreshPlan.levels`, `.items`, `.skipped_count`).

## Execution (`refresh_engine.execution`, internal but exported)

- `RefreshCoordinator` / `RefreshExecutor` implement overlap policy and bounded,
  retrying, dependency-ordered execution. You normally interact with these only
  through `RefreshEngine`.

## State (`refresh_engine.stores`)

- `InMemoryStateStore(initial: Mapping[str, ResourceState] | None = None)` — default store; copy-on-write with a single asyncio lock, serialized atomic commits via `InMemoryStateTransaction`.

## Scheduling (`refresh_engine.scheduling`)

- `AsyncScheduler(callback, interval, *, events=None, run_immediately=False, cancel_active_on_stop=False)` — asyncio-native interval scheduler built on APScheduler.
  - `async start() -> None`, `async pause() -> None`, `async resume() -> None`, `async stop() -> None`.
  - Raises `SchedulerError` for invalid state transitions (e.g. starting twice).
  - `interval` must be `> 0` (raises `ValueError` otherwise).
- `ExternalSchedulerAdapter(submit: Callable[[RefreshRequest], Awaitable[RefreshResult]])` — `async run(request) -> RefreshResult`; thin wrapper for externally owned timing (cron, k8s CronJob, another framework's scheduler).

## Triggers and selectors (`refresh_engine.triggers`)

- `EventTrigger(mapper: Callable[[object], RefreshRequest | None])` — `request_for(event) -> RefreshRequest | None`.
- `QueryTrigger()` — `request(resource_ids, *, metadata=None) -> RefreshRequest` (always `RefreshMode.TARGETED`, `TriggerSource.QUERY`).
- `IDSelector(resource_ids)` — `matches(resource) -> bool` by ID membership.
- `TagSelector(tags, match_all=True)` — matches `resource.metadata["tags"]`.
- `PredicateSelector(predicate: Callable[[Resource], bool])` — arbitrary predicate.

`RefreshEngine.refresh()` accepts resource IDs directly; selectors are reusable
matching utilities for your own trigger mapping or discovery filtering, not
engine constructor arguments.

## Events and observability (`refresh_engine.events`, `refresh_engine.observability`)

- `EventKind` (`StrEnum`): `REFRESH_STARTED`, `DISCOVERY_STARTED`, `DISCOVERY_COMPLETED`, `DETECTION_STARTED`, `DETECTION_COMPLETED`, `PLANNING_STARTED`, `PLANNING_COMPLETED`, `EXECUTION_STARTED`, `RESOURCE_REFRESH_STARTED`, `RESOURCE_REFRESH_COMPLETED`, `RESOURCE_REFRESH_FAILED`, `EXECUTION_COMPLETED`, `STATE_PERSISTED`, `REFRESH_COMPLETED`, `REFRESH_FAILED`, `REFRESH_CANCELLED`, `SCHEDULER_STARTED`, `SCHEDULER_STOPPED` (13 distinct kinds occur in a typical successful single-resource refresh, per the docs example).
- `RefreshEvent(kind, refresh_id=None, resource_id=None, timestamp=<utc now>, attributes={})`.
- `EventPublisher()` — `subscribe(handler)`, `unsubscribe(handler)`, `async publish(event)`, `async drain()`; `.errors` collects isolated handler exceptions. Handlers run in subscription order, off the refresh's own execution path.
- `StructlogEventHandler(logger=None, redactor=default_redactor)` — callable event handler; emits one `refresh_event` structlog record per lifecycle event.
- `default_redactor(values)` — redacts common secret-like keys (`api_key`, `authorization`, `password`, `secret`, `token`).
- `InMemoryMetrics()` — provider-neutral `MetricsSink`; `increment`, `observe`, `.snapshot()`. The engine does not emit into it automatically — an event handler must translate lifecycle events into metrics calls.

## Errors (`refresh_engine.errors`)

`RefreshError` (base) and: `ConfigurationError`, `DiscoveryError`,
`SnapshotError`, `FingerprintError`, `ChangeDetectionError`, `DependencyError`
(with subclass `DependencyCycleError(cycle: tuple[str, ...])`),
`PlanningError`, `ExecutionError`, `StateStoreError`, `SchedulerError`,
`CancellationError`.

## Extension ABCs (`refresh_engine.api.abc`, all optional)

`ResourceSourceABC`, `FingerprintStrategyABC`, `RefreshOperationABC`,
`StateTransactionABC` (provides a default commit-on-success /
rollback-on-exception `__aenter__`/`__aexit__`), `StateStoreABC`,
`ResourceSelectorABC`, `MetricsSinkABC`, `EventPublisherABC`,
`AsyncSchedulerABC`.
