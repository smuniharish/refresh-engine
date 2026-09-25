# Architecture

Source: `docs/architecture/overview.md`, `docs/architecture/lifecycle.md`,
`docs/architecture/decisions.md`, `src/refresh_engine/api/engine.py`.

## Component map

```
Application --> ResourceSource --+
Application --> RefreshOperation-+--> RefreshEngine --> FingerprintStrategy
                                        |            --> ChangeDetector
                                        |            --> DependencyGraph -> ImpactAnalyzer
                                        |            --> RefreshPlanner -> RefreshPlan
                                        |            --> RefreshExecutor -> RefreshOperation
                                        +--> StateStore (transactional)
                                        +--> EventPublisher -> EventHandler(s)
```

`RefreshEngine` is the only orchestration entry point. It owns a
`RefreshCoordinator` (overlap policy), a `ChangeDetector`, a `RefreshPlanner`, an
`ImpactAnalyzer`, and a `RefreshExecutor`; these are internal collaborators, not
part of the public extension surface (they are still exported for advanced
composition, e.g. building your own orchestration around `RefreshPlanner`
directly).

## Public contracts (protocols with optional ABCs)

| Contract | Protocol | ABC | Responsibility |
| --- | --- | --- | --- |
| Resource source | `ResourceSource` | `ResourceSourceABC` | `discover()` -> `DiscoveryResult`; `snapshot(resource)` -> `ResourceSnapshot` |
| Fingerprint strategy | `FingerprintStrategy` | `FingerprintStrategyABC` | `fingerprint(snapshot)` -> `ResourceFingerprint` |
| Refresh operation | `RefreshOperation` | `RefreshOperationABC` | `__call__(resource, snapshot, action, request)` -> `None` |
| State store | `StateStore` | `StateStoreABC` | `load_all()`, `transaction()` |
| State transaction | `StateTransaction` | `StateTransactionABC` | `put`, `delete`, `commit`, `rollback`, async context manager |
| Resource selector | `ResourceSelector` | `ResourceSelectorABC` | `matches(resource)` -> `bool` |
| Metrics sink | `MetricsSink` | `MetricsSinkABC` | `increment`, `observe` |
| Event publisher | (no separate protocol) | `EventPublisherABC` | `subscribe`, `unsubscribe`, `publish`, `drain` |
| Async scheduler | (no separate protocol) | `AsyncSchedulerABC` | `start`, `pause`, `resume`, `stop` |

Both integration styles are always available: implement the protocol
structurally (any object with matching methods satisfies it, including
`@runtime_checkable isinstance` checks), or inherit the ABC for
abstract-method enforcement and (for state transactions) a default
`__aenter__`/`__aexit__` that commits on success and rolls back on exception.
Built-in implementations (e.g. `CompositeHash`, `InMemoryStateStore`) inherit
the ABCs while still satisfying the protocols — you can mix custom protocol-only
objects with built-in ABC-based ones freely.

## Lifecycle (verified against `RefreshEngine._execute_lifecycle`)

```
REFRESH_STARTED
  -> discover()                (DISCOVERY_STARTED / DISCOVERY_COMPLETED)
  -> snapshot + fingerprint per resource (bounded by max_concurrency)
  -> DETECTION_STARTED -> ChangeDetector.detect(...) -> DETECTION_COMPLETED
  -> build DependencyGraph from previous+current state
  -> ImpactAnalyzer.analyze(changes, graph) -> ImpactSet
  -> PLANNING_STARTED -> RefreshPlanner.plan(...) -> RefreshPlan -> PLANNING_COMPLETED
  -> load any plan-only snapshots not already fetched
  -> EXECUTION_STARTED -> RefreshExecutor.execute(plan, ...) -> EXECUTION_COMPLETED
       (per resource: RESOURCE_REFRESH_STARTED / _COMPLETED / _FAILED)
  -> persist successful states in one StateStore transaction -> STATE_PERSISTED
  -> REFRESH_COMPLETED (or REFRESH_FAILED / REFRESH_CANCELLED)
-> RefreshResult
```

`RefreshResult` carries `status`, timing, `discovered_count`, `added_count`,
`modified_count`, `deleted_count`, `unchanged_count`, `impacted_count`,
`refreshed_count`, `skipped_count`, `failed_count`, `errors`, `warnings`, and
per-resource `executions`.

## Core invariants (do not violate these when extending the engine)

1. A source failure is an error, never an empty discovery (`DiscoveryError`
   aborts the refresh instead of being treated as "no resources").
2. An incomplete discovery (`DiscoveryResult(complete=False)`) never implies
   deletion of the resources it omitted.
3. A failed or cancelled refresh never advances a resource's successful state.
4. Fingerprints are deterministic and type-sensitive (see
   [fingerprinting.md](fingerprinting.md)).
5. Work is bounded by `RefreshConfig.max_concurrency`.
6. Dependencies execute before dependents; cycles are rejected.
7. Every background task (scheduler, event dispatch) has an owner and an
   explicit shutdown path.
8. Event subscriber failures are isolated (collected in
   `EventPublisher.errors`) and never mutate refresh state.

## Complexity

Discovery and change detection are `O(V)` in the number of resources; graph
construction and planning are `O(V + E)` with dependency edges `E`. Execution
creates at most `max_concurrency` concurrently active resource operations. The
engine retains compact `ResourceState` (fingerprint + small metadata), not
source payloads, between refreshes.

## Runtime dependencies

The package delegates to mature general-purpose libraries rather than
maintaining reduced in-house equivalents:

- **NetworkX** — dependency DAG algorithms (`DependencyGraph`).
- **Tenacity** — bounded asynchronous retry/backoff (`RefreshExecutor`).
- **APScheduler** — interval timing under `AsyncScheduler`.
- **structlog** — structured lifecycle logging (`StructlogEventHandler`); the
  engine does not configure global structlog processors/renderers — the
  application owns that.

## What the engine explicitly does NOT do

- It does not know what a "resource" means; that is entirely the consuming
  `ResourceSource`/`RefreshOperation`.
- It does not provide a database driver, message queue, or web framework
  integration.
- It does not run synchronous code off the event loop for you — long blocking
  calls inside your `ResourceSource`/`RefreshOperation` still block the loop.
- It does not retry indefinitely by default (`RetryConfig.max_attempts=1`
  unless configured otherwise).
