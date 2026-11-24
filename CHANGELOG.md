# Changelog

## [1.1.0] - 2026-11-24

### Added

- Live PyPI download badges on the README and documentation home page.
- A bounded event queue: `AsyncEventPublisher(capacity=...)` with a reserve for
  refresh boundary events, and `queued`, `peak_queued`, and `dropped` statistics.
- Exception redaction in structured logs: `redact_exception`, `redact_text`, and
  the `exception_redactor` and `raw_exceptions` options of
  `StructlogEventHandler`.
- Key-identity check in the `StateStoreContract` test suite.

### Improved

- Event, model, state, and request metadata is copied and frozen at every depth,
  so later changes to caller-owned containers never leak into the engine.
- `InMemoryStateStore` and the engine validate that every state is keyed by its
  resource ID.
- The selector warning of a targeted refresh reports how many resources were
  selected by ID.
- The engine yields to event delivery while the event queue is backlogged.
- Documentation of fail-fast semantics, asyncio and timeout limits, canonical
  fingerprint encoding, cheap-indicator dependencies, and metadata immutability.

### Fixed

- Exception messages and tracebacks in structured logs and package diagnostics
  pass through credential redaction.
- `DiscoveryResult.complete`, `Resource.cheap_indicator_reliable`, and
  `TagSelector.match_all` accept only actual booleans, so a value such as
  `"false"` can no longer infer deletions or skip snapshots.
- OpenTelemetry resource spans left open by a cancelled or timed-out refresh are
  ended by its terminal event, and a failing metric no longer leaves a span open.

## [1.0.1] - 2026-11-18

### Improved

- Updated compatible build, documentation, test, lint, and example dependency
  floors to current stable releases; verified remaining declarations are current.
- Kept PyPI publishing under maintainer control by removing the automated
  release-workflow publish job.
- Simplified public architecture diagrams to show conceptual flows while
  retaining the public lifecycle and change-detection guarantees.

## [1.0.0] - 2026-11-09

### Added

- ABC-based extension points for resource sources, operations, fingerprints,
  state stores, transactions, selectors, metrics sinks, event handlers, and event
  publishers.
- Async event handlers, metric writes, and state transaction acquisition.
- Full caller-supplied tenacity retry policies.
- Optional OpenTelemetry metrics and refresh/resource spans.
- A reusable state-store contract test suite.
- Python 3.15 support.
- Zensical documentation, validated Agent Skills, and expanded package checks.

### Improved

- Dependency-aware refresh execution, cancellation handling, and persistence
  diagnostics.
- SQLite connection cleanup and PostgreSQL pool reuse in the storage examples.
- Dependency bounds, security auditing, documentation links, and built-package
  verification.
