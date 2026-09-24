# Architecture decisions

The following decisions define the package boundary and the guarantees applications
can rely on. Each entry records the chosen design, its rationale, and its operational
effect.

## Domain-neutral core

**Decision:** Model sources, operations, fingerprints, selectors, state stores, and
events as generic contracts. The core has no dependency on an application framework
or resource domain.

**Rationale:** Refresh mechanics recur across configuration, document, plugin, and
registry systems. Domain semantics and side effects belong to the integrating
application.

**Operational effect:** Applications provide small adapters while the package remains
independently reusable and testable.

## Async-first execution

**Decision:** Use asyncio throughout discovery, snapshotting, execution, scheduling,
and shutdown. No thread-based scheduler or internal event-loop bridge is provided.

**Rationale:** Refresh workloads are primarily I/O-bound and require native
cancellation and bounded concurrency.

**Operational effect:** Synchronous applications own their async integration boundary,
usually through an application event loop or external scheduler.

## Explicit scheduler ownership

**Decision:** Provide `AsyncScheduler` with explicit start, pause, resume, and stop
operations, plus `ExternalSchedulerAdapter` for externally managed timing.

**Rationale:** Fire-and-forget tasks make shutdown, cancellation, and error ownership
ambiguous.

**Operational effect:** Every scheduled task has a clear owner and deterministic
shutdown path.

## Fingerprint-based incremental refresh

**Decision:** Persist deterministic, pluggable fingerprints and compare them on later
refreshes.

**Rationale:** Reprocessing every resource does not scale. Timestamps alone are not
portable or sufficiently reliable.

**Operational effect:** Unchanged resources skip application work. Fingerprint
strategies must include every value relevant to refresh behavior.

## Reliable cheap pre-checks

**Decision:** Skip snapshotting and strong hashing only when a source marks an equal
cheap indicator as reliable.

**Rationale:** Version tokens and ETags can avoid expensive reads, but an unreliable
hint must never weaken default correctness.

**Operational effect:** Incorrectly declaring an indicator reliable can hide changes
and is therefore a source contract violation.

## Dependency-aware planning

**Decision:** Track dependency and dependent relationships, calculate transitive
impact, and execute topological levels in dependency-first order.

**Rationale:** A direct change can invalidate resources that depend on it.

**Operational effect:** Only affected resources run. Dependency cycles are rejected,
and applications must provide accurate dependency IDs.

## Configurable overlap behavior

**Decision:** Support `SKIP_IF_RUNNING`, `QUEUE`, `COALESCE`, and `CANCEL_PREVIOUS`
through one request coordinator.

**Rationale:** Deployments need different latency, throughput, and freshness
trade-offs when triggers overlap.

**Operational effect:** Coalescing merges compatible intent; cancellation requires
cooperative, idempotent operations.

## Transactional, pluggable state

**Decision:** Define async `StateStore` and `StateTransaction` contracts and provide
`InMemoryStateStore` as the default implementation.

**Rationale:** Persistence, pooling, credentials, schema migration, and database
operations vary by deployment.

**Operational effect:** Applications inject durable adapters without adding a database
driver to every installation. Successful state is committed atomically; the in-memory
default resets when the process exits.

## Safe discovery completeness

**Decision:** Require discovery to report whether its result is complete. Discovery
errors abort change detection, and incomplete results never infer deletion.

**Rationale:** Source failure, pagination truncation, and permission errors can
otherwise resemble mass deletion.

**Operational effect:** Real deletions wait for a complete discovery; accidental
deletions after partial discovery are prevented.

## Cancellation-safe shutdown

**Decision:** Propagate cancellation, await owned tasks, and roll back uncommitted
state.

**Rationale:** Suppressed cancellation can leak tasks or record work that did not
complete.

**Operational effect:** Shutdown remains deterministic and interrupted resources stay
eligible for retry.

## Application-managed durable adapters

**Decision:** Keep SQLite and PostgreSQL adapters outside the production package.
Expose stable state-store contracts instead.

**Rationale:** Shipping a database adapter would impose its driver, migration model,
pooling policy, and operational trade-offs on unrelated applications.

**Operational effect:** Applications own durable storage. Reference integrations
demonstrate restart continuity, atomic commit, and rollback.

## Established ecosystem primitives

**Decision:** Use NetworkX for graph algorithms, Tenacity for retries, APScheduler for
interval scheduling, and structlog for structured logging.

**Rationale:** These are mature general-purpose concerns whose local reimplementation
would add maintenance and correctness risk.

**Operational effect:** The package retains only refresh-specific translation,
lifecycle, state-consistency, and cancellation logic. Runtime dependencies are pinned
to compatible major versions and reviewed during upgrades.
