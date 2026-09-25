---
name: refresh-engine
description: Integrate, configure, debug, test, or extend the refresh-engine Python package for discovering resources, detecting what changed, and safely executing application-defined refresh operations — including incremental refresh, fingerprint/hash-based change detection, dependency-aware refresh, asyncio scheduling, bounded concurrency, and transactional state. Use when a resource population must be kept in sync without reprocessing everything or coupling this infrastructure to a specific domain (MCP, agents, files, etc.).
---

# refresh-engine

Use this skill for the existing `refresh_engine` Python package (PyPI:
`refresh-engine`), not to design a new hashing framework, scheduler,
dependency resolver, or state store when this package already provides one.

refresh-engine's public API is imported directly from the top-level package:

```python
from refresh_engine import RefreshEngine, ...
```

It is **domain-agnostic refresh infrastructure**: it discovers resources,
fingerprints and diffs them against previous state, plans dependency-aware
work, and executes an **application-supplied** `RefreshOperation` safely under
bounded concurrency, retries, and transactional state commits. It has no
concept of MCP, agents, skills, files, or any other resource domain — the
consuming application supplies that meaning through exactly two contracts,
`ResourceSource` and `RefreshOperation`. Everything else (fingerprinting,
change detection, dependency impact, planning, execution, state, scheduling)
is reusable infrastructure that should not be duplicated or domain-coupled.

Read [`references/architecture.md`](references/architecture.md) before
reasoning about internal behavior. Read
[`references/integration.md`](references/integration.md) before adding it to
an application.

## Activate when

Use refresh-engine when an application must keep a destination in sync with a
changing collection of external resources (configs, documents, registry
entries, catalog items, plugin manifests, cache entries, etc.) without
reprocessing every resource on every refresh, and without hand-rolling
change-detection, dependency ordering, or scheduling.

Typical indicators:

- a resource population is large and only a small fraction typically changes
  between refreshes;
- a change in one resource must propagate to resources that depend on it;
- refresh must run periodically without blocking the application's main
  execution path;
- a previous attempt manually diffs/hashes resources or reimplements
  scheduling instead of using an existing library;
- an existing `RefreshEngine` integration needs configuration, debugging, or
  tests.

Do not select it merely because an application has one resource with no
independent change signal (just re-fetch and overwrite), needs pure
request/response TTL caching (use a cache library instead), or wants a
general-purpose task scheduler unrelated to resource refresh.

## Required workflow

### Before changing an application

1. Inspect the installed/current refresh-engine version and the project's
   existing `RefreshEngine` construction, if any. In this repository,
   [`pyproject.toml`](https://github.com/smuniharish/refresh-engine/blob/main/pyproject.toml)
   (`version = "0.1.0"`, `requires-python = ">=3.12,<3.13"`) and
   [`src/refresh_engine/__init__.py`](https://github.com/smuniharish/refresh-engine/blob/main/src/refresh_engine/__init__.py)
   are the version/export sources; verify `refresh_engine.__all__` against the
   installed package rather than assuming a symbol exists.
2. Identify resource identity (`resource_id`) and where discovery and reads
   come from — this becomes the application's `ResourceSource`.
3. Decide whether a cheap, reliable change signal exists (an ETag or version
   token from the source system) for `Resource.cheap_indicator` /
   `cheap_indicator_reliable` — only mark it reliable when equality *proves*
   the content cannot have changed.
4. Start from the repository example that matches the workload; see
   [`references/integration.md`](references/integration.md) and the
   [example collection](https://github.com/smuniharish/refresh-engine/tree/main/examples).
5. Use the documented public contracts and configuration only
   (`RefreshEngine`, `RefreshConfig`, `ResourceSource`/`RefreshOperation`,
   `FingerprintStrategy`, `StateStore`, `AsyncScheduler`). The package has no
   CLI and no plugin registry.

### Choose the right response to a refresh requirement

1. **Re-run every resource's operation every time, regardless of change:**
   `RefreshMode.FULL`.
2. **Only resources changed since the last successful refresh (the common
   case, and the default):** `RefreshMode.INCREMENTAL` — see
   [`references/incremental-refresh.md`](references/incremental-refresh.md).
3. **A specific, caller-known set of resource IDs:** `RefreshMode.TARGETED`
   with `resource_ids` set.
4. **Changes must propagate to resources that depend on them:**
   `RefreshMode.DEPENDENCY_AWARE` — see
   [`references/dependency-refresh.md`](references/dependency-refresh.md).
5. **Refresh must not block the caller and must run periodically:** use
   `AsyncScheduler` (in-process, same event loop) or an external scheduler
   plus `ExternalSchedulerAdapter` — never a manual thread/`while True` loop.
   See [`references/scheduling.md`](references/scheduling.md).
6. **Discovery or a resource operation failed:** never infer that resources
   were deleted from a `DiscoveryError` or a `complete=False` discovery, and
   never advance a resource's persisted state past a failed operation. See
   [`references/state-management.md`](references/state-management.md).
7. **Choosing or writing a fingerprint strategy:** the default
   `CompositeHash` covers common content shapes; write a custom
   `FingerprintStrategy` only for content it cannot canonicalize. See
   [`references/fingerprinting.md`](references/fingerprinting.md).

## Integration rules

- Implement exactly two contracts per integration — `ResourceSource` and
  `RefreshOperation` (structurally, via `Protocol`, or via `ResourceSourceABC`
  / `RefreshOperationABC`) — and keep all domain-specific translation inside
  them (Pattern E in
  [`references/integration.md`](references/integration.md)). Do not let
  `refresh_engine` types leak into unrelated application layers.
- Bound concurrency through `RefreshConfig.max_concurrency`; never spawn
  unbounded concurrent refresh tasks around the engine.
- Persist state only through the engine's own transactional commit (after a
  resource's operation succeeds) — never write your own parallel "last
  refreshed" bookkeeping.
- Make every `RefreshOperation` idempotent and cancellation-safe; a
  `CancelledError` must not leave partially-applied, persisted state.
- Configure `RefreshConfig.overlap_policy` deliberately
  (`COALESCE`/`SKIP_IF_RUNNING`/`QUEUE`/`CANCEL_PREVIOUS`) instead of guarding
  overlapping refreshes yourself.
- Use `EventPublisher`/`StructlogEventHandler`/`MetricsSink` for observability
  instead of ad hoc logging scattered through your integration.

## Prohibited shortcuts

Do **not**:

- reimplement manual hashing/diffing instead of using a `FingerprintStrategy`
  and the built-in `ChangeDetector`;
- block the caller's main execution path for scheduled refresh instead of
  using `AsyncScheduler` or `ExternalSchedulerAdapter`;
- use `RefreshMode.FULL` "to be safe" when `INCREMENTAL` or
  `DEPENDENCY_AWARE` is correct and far cheaper at scale;
- persist a new fingerprint or state entry before the corresponding operation
  succeeds, or persist state after `CancelledError`/an exception;
- spawn unbounded concurrent tasks around the engine instead of using
  `RefreshConfig.max_concurrency`;
- treat a `DiscoveryError` or a `complete=False` discovery result as "every
  existing resource was deleted";
- couple `refresh_engine` types to a specific domain/framework (MCP, agents, a
  particular web framework) anywhere outside a single adapter layer;
- invent an import, configuration option, or API not present in
  `refresh_engine.__all__` or the installed source.

## Verification checklist

For an application change, add or update a focused test that exercises the
relevant refresh path — added/modified/deleted/unchanged resources,
dependency-aware ordering, scheduler start/stop, bounded concurrency, or
failure/cancellation handling — following the patterns in
[`references/integration.md`](references/integration.md). Run the project's
own format, lint, type, and test commands.

For changes to this skill, follow
[`../../validation/README.md`](../../validation/README.md). Consult the
authoritative
[refresh-engine documentation](https://refresh-engine.readthedocs.io) and
[example collection](https://github.com/smuniharish/refresh-engine/tree/main/examples)
rather than expanding this file into a second manual.
