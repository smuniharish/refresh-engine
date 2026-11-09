# Changelog

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
