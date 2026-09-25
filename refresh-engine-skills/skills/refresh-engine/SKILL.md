---
name: refresh-engine
description: >
  Use when integrating, extending, configuring, debugging, testing, or designing
  refresh workflows with the refresh-engine Python package (PyPI: refresh-engine,
  import: refresh_engine) — including incremental refresh, hash/fingerprint-based
  change detection, dependency-aware refresh, asyncio scheduling, bounded
  concurrency, transactional state stores, and refresh lifecycle events. Trigger
  on mentions of refresh-engine, RefreshEngine, resource discovery + change
  detection pipelines, or "refresh N resources without reprocessing everything."
license: Apache-2.0
metadata:
  package: refresh-engine
  import: refresh_engine
  tracks-package-version: "0.1.x"
  docs: https://refresh-engine.readthedocs.io
---

# refresh-engine

`refresh-engine` is a **domain-agnostic, asyncio-first Python library** (PyPI:
`refresh-engine`, import `refresh_engine`, requires Python 3.12) that discovers
resources, detects what changed, plans dependency-aware work, and executes
**application-defined** refresh operations safely. It is generic refresh
*infrastructure* — it has no concept of MCP, agents, skills, files, APIs, or any
other resource domain. The consuming application supplies the domain meaning
(what a "resource" is) and the actual refresh side effect; the engine supplies
discovery orchestration, fingerprinting, change detection, dependency impact
analysis, planning, bounded concurrent execution, transactional state, retries,
timeouts, cancellation, and lifecycle events.

**Do not couple this package to a specific domain** (e.g. "an MCP refresh engine"
or "an agent-skills refresh engine"). Keep domain semantics in the consumer's
`ResourceSource` and `RefreshOperation` implementations; treat `refresh-engine`
itself as reusable infrastructure.

## When to use this skill

- Building or modifying an integration that periodically re-syncs a collection of
  external resources (configs, documents, registry entries, plugin manifests,
  cache entries, catalog items, etc.) into some destination.
- Choosing a refresh mode (full / incremental / targeted / dependency-aware).
- Adding hash/fingerprint-based change detection to avoid reprocessing unchanged
  resources.
- Adding dependency-aware refresh so changes propagate to dependents.
- Adding background/scheduled refresh without blocking the caller.
- Debugging discovery, change-detection, or state-persistence behavior.
- Writing tests that exercise the refresh lifecycle.

## When NOT to use this skill / this package

- A single resource with no independent change signal and no reuse across
  refreshes (just re-fetch and overwrite in application code).
- Pure request/response caching with TTL semantics — use a cache library.
- You need something *other* than "detect what changed among many resources,
  then run my callback for the ones that changed" — this package is narrowly
  scoped to that problem.
- Don't build a custom hashing/scheduling/dependency framework "just in case"
  when this package already provides one — see
  [references/troubleshooting.md](references/troubleshooting.md) anti-patterns.

## Core mental model (lifecycle)

```
Trigger (manual / scheduled / event / query / external)
   -> RefreshRequest
   -> discover()               [ResourceSource -> DiscoveryResult, complete flag]
   -> snapshot() + fingerprint [per resource, unless a reliable cheap_indicator matches]
   -> change detection         [ADDED / MODIFIED / DELETED / UNCHANGED / DEPENDENCY_CHANGED]
   -> dependency impact        [DependencyGraph -> ImpactAnalyzer -> ImpactSet]
   -> planning                 [RefreshPlanner -> RefreshPlan: topological levels + PlanAction]
   -> execution                [RefreshExecutor: bounded concurrency, retries, timeouts]
   -> state commit             [StateStore transaction — only for succeeded resources]
   -> RefreshResult + lifecycle events
```

Full detail: [references/architecture.md](references/architecture.md) and
[references/api-reference.md](references/api-reference.md).

## Minimal integration

```python
import asyncio
from refresh_engine import (
    DiscoveryResult, PlanAction, RefreshEngine, Resource, ResourceSnapshot,
)

class Source:
    async def discover(self) -> DiscoveryResult:
        async def resources():
            yield Resource("alpha")
        return DiscoveryResult(resources())

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        return ResourceSnapshot(resource.resource_id, content={"value": 1})

async def apply(resource, snapshot, action, request) -> None:
    if action is PlanAction.DELETE:
        assert resource is not None
        return  # resource.resource_id only; snapshot is always None for DELETE
    assert resource is not None and snapshot is not None
    print(action, resource.resource_id, snapshot.content)

async def main() -> None:
    engine = RefreshEngine(Source(), apply)
    result = await engine.refresh()  # mode defaults to RefreshMode.INCREMENTAL
    print(result.status, result.refreshed_count)
    await engine.close()

asyncio.run(main())
```

You implement exactly two contracts (`ResourceSource`, `RefreshOperation`);
everything else has a working default (`CompositeHash` fingerprint,
`InMemoryStateStore`, default `RefreshConfig`, default `EventPublisher`).

## Critical architecture rules (verified against source/tests)

1. **A source failure is an error, never an empty discovery.** `discover()`
   raising is wrapped as `DiscoveryError` and aborts the refresh; it must never
   be interpreted as "zero resources exist."
2. **An incomplete discovery never implies deletion.** `DiscoveryResult(complete=False)`
   suppresses deletion inference for IDs missing from that discovery; only a
   `complete=True` discovery may plan `PlanAction.DELETE`.
3. **State only advances after the corresponding operation succeeds.** A failed,
   cancelled, or timed-out resource keeps its previous `ResourceState`.
4. **Fingerprints must be deterministic.** Built-in strategies canonicalize
   supported types (str/int/float/bool/bytes/date/datetime/Path/Enum/dataclass/
   Mapping/Set/Sequence); non-finite floats and unsupported types raise
   `FingerprintError`. Never fingerprint clocks, randomness, or open handles.
5. **A reliable `cheap_indicator` skips snapshotting**, not just hashing — set
   `Resource.cheap_indicator_reliable=True` only when equality *proves* the
   underlying content cannot have changed (e.g. an ETag/version token from the
   source system), never as an optimistic guess.
6. **Dependencies execute before dependents.** `RefreshPlan.levels` are
   topological generations from `DependencyGraph`; a cycle raises
   `DependencyCycleError`.
7. **Work is bounded by `RefreshConfig.max_concurrency`** (default 10) — never
   spawn unbounded concurrent refresh tasks around the engine.
8. **Cancellation is cooperative and rolls back.** `CancelledError` propagates
   through discovery/snapshot/execution; uncommitted state is not persisted.
9. **`RefreshEngine.refresh()`/`submit()` overlap is governed by
   `RefreshConfig.overlap_policy`** (`COALESCE` by default): `SKIP_IF_RUNNING`,
   `QUEUE`, `COALESCE`, or `CANCEL_PREVIOUS`.
10. **Scheduling is asyncio-native and explicit.** `AsyncScheduler` has
    `start`/`pause`/`resume`/`stop`; there is no thread-based or fire-and-forget
    scheduler. Applications own any sync-to-async boundary.

See [references/failure-handling.md](references/failure-handling.md) and
[references/state-management.md](references/state-management.md) for the exact
mechanics, and [references/troubleshooting.md](references/troubleshooting.md)
for symptoms mapped to causes.

## Refresh mode decision tree

```
Need to keep a destination in sync with changing resources?
  no  -> don't use refresh-engine
  yes ->
    Re-run every resource's operation every time, regardless of change?
      yes -> RefreshMode.FULL
    Only resources that changed since the last successful refresh?
      yes -> RefreshMode.INCREMENTAL (default)
    A specific, caller-known set of resource IDs?
      yes -> RefreshMode.TARGETED (resource_ids required)
    Changes must propagate to resources that depend on them?
      yes -> RefreshMode.DEPENDENCY_AWARE
```

Detail and verified output: [references/incremental-refresh.md](references/incremental-refresh.md).

## API surface you will use most often

| Concern | Symbols (import from `refresh_engine`) |
| --- | --- |
| Engine | `RefreshEngine`, `RefreshConfig`, `RefreshRequest`, `RefreshResult` |
| Domain contracts | `ResourceSource` / `ResourceSourceABC`, `RefreshOperation` / `RefreshOperationABC` |
| Models | `Resource`, `ResourceSnapshot`, `DiscoveryResult`, `ResourceState`, `Change`, `ChangeSet`, `ChangeType`, `ImpactSet`, `PlanAction` |
| Modes/policy | `RefreshMode`, `TriggerSource`, `OverlapPolicy`, `FailurePolicy`, `RetryConfig`, `TimeoutConfig` |
| Fingerprints | `CompositeHash` (default), `ContentHash`, `MetadataHash`, `VersionTokenFingerprint`, `ETagFingerprint`, `FingerprintStrategy` / `FingerprintStrategyABC` |
| Dependencies | `DependencyGraph`, `ImpactAnalyzer`, `RefreshPlanner` |
| State | `StateStore` / `StateStoreABC`, `StateTransaction` / `StateTransactionABC`, `InMemoryStateStore` (default) |
| Scheduling | `AsyncScheduler`, `ExternalSchedulerAdapter` |
| Triggers/selectors | `EventTrigger`, `QueryTrigger`, `IDSelector`, `TagSelector`, `PredicateSelector` |
| Events/observability | `EventPublisher`, `RefreshEvent`, `EventKind`, `StructlogEventHandler`, `default_redactor`, `InMemoryMetrics`, `MetricsSink` / `MetricsSinkABC` |
| Errors | `RefreshError` and subclasses: `DiscoveryError`, `SnapshotError`, `FingerprintError`, `ChangeDetectionError`, `DependencyError`/`DependencyCycleError`, `PlanningError`, `ExecutionError`, `StateStoreError`, `SchedulerError`, `CancellationError`, `ConfigurationError` |

Full reference with signatures: [references/api-reference.md](references/api-reference.md).
Never invent a symbol not listed above or in that reference — verify against
`refresh_engine.__all__` or the installed source before using an API.

## Agent workflow

1. Inspect the consuming project: what needs to be kept fresh, and why.
2. Identify resource identity (`resource_id`) and where discovery + read come from.
3. Decide whether a cheap, reliable change signal exists (ETag/version token) for
   `Resource.cheap_indicator` / `cheap_indicator_reliable`.
4. Pick a refresh mode from the decision tree above.
5. Choose or write a `FingerprintStrategy` if the default `CompositeHash` is
   wrong for your content shape.
6. Implement `RefreshOperation`, handling `PlanAction.REFRESH` and `PlanAction.DELETE`
   distinctly; make it idempotent and cancellation-safe.
7. Choose a `StateStore` (default `InMemoryStateStore`, or inject a durable
   adapter — see [references/state-management.md](references/state-management.md)).
8. Configure scheduling if periodic refresh is needed
   ([references/scheduling.md](references/scheduling.md)) — never block the
   caller's main path.
9. Set `RefreshConfig` (`max_concurrency`, `overlap_policy`, `failure_policy`,
   `retry`, `timeout`) — see [references/concurrency.md](references/concurrency.md).
10. Add tests covering added/modified/deleted/unchanged/dependency-changed
    resources, failure handling, and (if scheduled) start/stop —
    [references/testing.md](references/testing.md).
11. Run `pytest`, `ruff`, and any relevant example to validate behavior.

## Common mistakes to avoid

- Reimplementing manual hashing/diffing instead of `FingerprintStrategy` +
  `ChangeDetector`.
- Blocking the caller for scheduled refresh (spin up `AsyncScheduler` or an
  external scheduler + `ExternalSchedulerAdapter` instead).
- Using `RefreshMode.FULL` "to be safe" when `INCREMENTAL` or
  `DEPENDENCY_AWARE` is correct and far cheaper at scale.
- Persisting state before the operation succeeds, or persisting on
  `CancelledError`/exception.
- Spawning unbounded tasks around the engine instead of using
  `RefreshConfig.max_concurrency`.
- Treating a `DiscoveryError` or `complete=False` result as "everything was
  deleted."
- Coupling the engine's public contracts to a specific framework (MCP, agents,
  web framework) instead of keeping that translation in the application layer.
- Inventing an API that isn't in `refresh_engine.__all__`.

## Reference index

- [references/architecture.md](references/architecture.md) — lifecycle, contracts, invariants, runtime dependencies.
- [references/api-reference.md](references/api-reference.md) — verified public API surface with signatures.
- [references/incremental-refresh.md](references/incremental-refresh.md) — full vs incremental vs targeted vs dependency-aware.
- [references/fingerprinting.md](references/fingerprinting.md) — fingerprint strategies and cheap indicators.
- [references/dependency-refresh.md](references/dependency-refresh.md) — dependency graph, impact analysis, planning.
- [references/scheduling.md](references/scheduling.md) — `AsyncScheduler`, `ExternalSchedulerAdapter`, triggers.
- [references/concurrency.md](references/concurrency.md) — `max_concurrency`, overlap policy, retries, timeouts.
- [references/state-management.md](references/state-management.md) — `StateStore`/`StateTransaction`, durable adapters.
- [references/failure-handling.md](references/failure-handling.md) — failure policy, partial discovery, cancellation.
- [references/testing.md](references/testing.md) — what to test and with which fixtures.
- [references/integration-patterns.md](references/integration-patterns.md) — five integration patterns (A–E).
- [references/troubleshooting.md](references/troubleshooting.md) — problem/cause/diagnosis/solution and anti-patterns.

## Examples

- [examples/basic.md](examples/basic.md) — first refresh, full/targeted modes.
- [examples/incremental.md](examples/incremental.md) — change detection at scale.
- [examples/scheduled.md](examples/scheduled.md) — background scheduling, external scheduling.
- [examples/dependency-aware.md](examples/dependency-aware.md) — dependency graphs and impact.
- [examples/custom-extension.md](examples/custom-extension.md) — ABC-based extension points, custom stores.

Every example is copied from, or executed against, `examples/*.py` in the main
package and verified to run with the installed `refresh-engine==0.1.0`.

## Source of truth

Primary sources, in order of precedence: `tests/` > `src/refresh_engine/` >
`examples/*.py` > `docs/` (mkdocs) > https://refresh-engine.readthedocs.io. If
this skill and the installed package ever disagree, trust the installed package
and file the discrepancy rather than guessing.
