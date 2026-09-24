# Changelog

All notable changes are documented here using
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) conventions. This project
uses semantic versioning.

## [Unreleased]

## [0.1.0] - 2026-09-24

### Added

- User, contributor, security, architecture, API, and task-oriented guide
  documentation.
- Domain-neutral examples for supported integration scenarios.
- Reproducible benchmark harness for 1k, 10k, and 100k resource workloads and
  multiple change profiles.
- Application-owned SQLite and PostgreSQL store examples demonstrating atomic commits,
  restart continuity, rollback, and public `StateStoreABC` injection.
- Trusted Publishing release workflow.
- Optional abstract base classes for sources, fingerprints, operations, selectors,
  state stores/transactions, events, metrics, and async schedulers.
- Native required `structlog` integration for structured lifecycle logging.
- NetworkX-backed dependency graph algorithms, Tenacity-backed retry policies, and
  APScheduler-backed interval scheduling.
- Async-only scheduler surface; applications own any synchronous integration boundary.
- Read the Docs configuration, public-API-only reference pages, copyable examples, and
  observed output.
- Cross-platform CI, dependency auditing, automated dependency updates, and clean
  distribution smoke tests.
- Initial asyncio-first refresh engine with discovery, fingerprinting,
  dependency-aware planning, bounded execution, transactional state, events,
  triggers, and schedulers.
