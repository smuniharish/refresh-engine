# Troubleshooting

Grounded in `docs/architecture/decisions.md`, `docs/guides/*`, and the
package's own tests/examples. Format: Problem / Cause / Diagnosis / Solution.

## Everything is reported as `MODIFIED` every refresh

- **Cause:** the fingerprint strategy includes a non-deterministic or
  ever-changing value (timestamp, random ID, unordered collection with
  unstable iteration, an object without a supported canonical form).
- **Diagnosis:** print/log the `ResourceFingerprint.value` across two
  back-to-back refreshes with no real source change; if it differs, inspect
  exactly what your `ResourceSnapshot.content`/`metadata` contains.
- **Solution:** exclude volatile fields from the snapshot passed into
  fingerprinting, or use `ContentHash`/`MetadataHash`/a custom
  `FingerprintStrategy` that normalizes/sorts unstable structures. See
  [fingerprinting.md](fingerprinting.md).

## `FingerprintError: unsupported fingerprint value`

- **Cause:** `snapshot.content`/`metadata` contains a type outside the
  supported set (see [fingerprinting.md](fingerprinting.md)) — e.g. a custom
  object, an open file handle, or a non-finite float.
- **Solution:** convert to a supported type (dict/list/str/dataclass/etc.)
  before constructing the `ResourceSnapshot`, or write a custom
  `FingerprintStrategy` that performs the conversion.

## Resources are unexpectedly deleted after a refresh

- **Cause:** `DiscoveryResult.complete` was `True` when the discovery was
  actually partial (e.g. pagination truncated early, a filter narrowed
  results, a permission error silently dropped some items).
- **Diagnosis:** check whether every expected resource ID was actually
  yielded by `discover()`, and whether any exception during pagination was
  swallowed instead of raised.
- **Solution:** set `complete=False` whenever the discovery cannot vouch for
  having seen every resource; only set `complete=True` when you are certain.
  See [state-management.md](state-management.md).

## Resources are never deleted even though the source removed them

- **Cause:** discovery is (correctly, or incorrectly) reporting
  `complete=False`.
- **Diagnosis:** confirm whether your source can actually guarantee
  completeness; if it can, fix it to report `complete=True`.
- **Solution:** only report `complete=True` once you've fixed the underlying
  completeness guarantee — do not force it to unblock deletions if the
  underlying discovery is genuinely unreliable; that reintroduces the exact
  risk `complete=False` protects against.

## A resource that should be unchanged keeps re-running

- **Cause 1:** `cheap_indicator_reliable=True` but the indicator value is not
  actually stable/monotonic for an unchanged resource (e.g. a `last_modified`
  header that changes on read).
- **Cause 2:** dependency set changed even though content didn't
  (`ChangeType.DEPENDENCY_CHANGED` still selects the resource in
  `INCREMENTAL`/`DEPENDENCY_AWARE` mode).
- **Solution:** verify the cheap indicator's actual guarantees with the
  source system; if dependencies legitimately changed, that re-run is
  correct behavior, not a bug.

## Scheduled refresh appears to block the application

- **Cause:** calling `await engine.refresh()` directly on a blocking timer
  (e.g. `time.sleep` in a loop) instead of using `AsyncScheduler` or an
  external scheduler.
- **Solution:** use `AsyncScheduler(callback, interval=...)` or
  `ExternalSchedulerAdapter` — see [scheduling.md](scheduling.md). Confirm
  `await scheduler.start()` returns immediately in your logs/tracing.

## `SchedulerError: scheduler is already started`

- **Cause:** calling `scheduler.start()` twice without an intervening
  `stop()`.
- **Solution:** guard start calls with your own state tracking, or call
  `stop()` before a second `start()`.

## Concurrent refresh requests seem to "disappear" or return unexpected results

- **Cause:** default `OverlapPolicy.COALESCE` merges concurrent requests into
  one run and returns the same `RefreshResult` to every caller.
- **Diagnosis:** check `RefreshConfig.overlap_policy`; log `RefreshRequest`
  identity/`correlation_id` to see merges happening.
- **Solution:** choose `QUEUE` if every request must run to completion
  independently, or `SKIP_IF_RUNNING`/`CANCEL_PREVIOUS` per your freshness vs.
  throughput trade-off. See [scheduling.md](scheduling.md).

## State is lost after a restart

- **Cause:** using the default `InMemoryStateStore` (process-local, in
  memory) in a context that requires durability across restarts.
- **Solution:** inject a durable `StateStore` (SQLite/PostgreSQL/etc.) — see
  [state-management.md](state-management.md); the package intentionally does
  not ship one.

## An operation runs twice for the same resource

- **Cause:** a timeout or retry re-invoked `RefreshOperation` after the
  external side effect had already completed but before the engine observed
  success.
- **Solution:** operations must be idempotent; use destination-side
  idempotency keys when available. This is expected engine behavior, not a
  bug — see [state-management.md](state-management.md).

---

## Architectural anti-patterns (verified against actual design)

1. **Reimplementing change detection manually** instead of using
   `FingerprintStrategy` + `ChangeDetector` — duplicates logic the package
   already provides and loses its determinism guarantees.
2. **Blocking the caller for scheduled refresh** — always use
   `AsyncScheduler`/`ExternalSchedulerAdapter`.
3. **Defaulting to `RefreshMode.FULL`** when `INCREMENTAL`/`DEPENDENCY_AWARE`
   is appropriate — wastes work at scale.
4. **Persisting a new fingerprint/state before the operation succeeds** —
   the engine already prevents this; do not add your own eager writes that
   bypass the transaction the engine gives you.
5. **Spawning unbounded concurrent refresh tasks** instead of using
   `RefreshConfig.max_concurrency`.
6. **Coupling `refresh_engine` types to a specific domain/framework** (MCP,
   agents, a particular web framework) anywhere outside a single adapter
   layer (Pattern E in [integration.md](integration.md)).
7. **Inventing APIs not present in `refresh_engine.__all__`** — always verify
   against the installed package
   (`python -c "import refresh_engine; print(sorted(refresh_engine.__all__))"`)
   or the published
   [API reference](https://refresh-engine.readthedocs.io/en/latest/api/)
   before use.

## Also avoid over-engineering

Do not add a custom event bus, database, queue, or additional background
worker pool "just in case" when `EventPublisher`, `StateStore`, and
`RefreshConfig.max_concurrency` already solve the requirement — see
[architecture.md](architecture.md) for what the engine already owns.
