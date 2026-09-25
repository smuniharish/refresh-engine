# Integration and testing

Source: `README.md`, `docs/architecture/overview.md`,
[examples](https://github.com/smuniharish/refresh-engine/tree/main/examples),
`tests/helpers.py`, and the package's own integration/scheduler/concurrency
test suites. Every pattern below is domain-neutral — none reference any
specific resource domain (MCP, agents, files, etc.); that translation belongs
entirely in your `ResourceSource`/`RefreshOperation`.

## Install

```bash
pip install refresh-engine
```

Requires Python 3.12 (`pyproject.toml`: `requires-python = ">=3.12,<3.13"`).
The full published documentation is at
[refresh-engine.readthedocs.io](https://refresh-engine.readthedocs.io); the
current example collection is at
[github.com/smuniharish/refresh-engine/tree/main/examples](https://github.com/smuniharish/refresh-engine/tree/main/examples).

## Pattern A — Basic refresh

```
Application -> ResourceSource + RefreshOperation -> RefreshEngine -> RefreshResult
```

Use when a single one-shot or manually triggered refresh is enough (no
scheduling, no dependency modeling). See
[`examples/01_basic_and_modes.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/01_basic_and_modes.py).

## Pattern B — Incremental refresh

```
Source -> snapshot -> FingerprintStrategy -> ChangeDetector -> ChangeSet -> RefreshOperation (changed only)
```

Use when the resource population is large or the operation is expensive, and
most resources are unchanged between refreshes. This is the default
(`RefreshMode.INCREMENTAL`) — you get it for free by doing nothing extra,
though you may want a reliable `cheap_indicator` to avoid snapshotting
unchanged resources too. See
[`examples/10_scale_and_genericity.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/10_scale_and_genericity.py)
and [incremental-refresh.md](incremental-refresh.md).

## Pattern C — Scheduled refresh

```
Application -> AsyncScheduler (background, same event loop) -> engine.refresh(trigger=SCHEDULED)
            -> or: external scheduler -> ExternalSchedulerAdapter -> engine.submit(request)
```

Use when refresh must happen periodically without blocking the caller's main
execution path. See
[`examples/04_async_and_external_scheduling.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/04_async_and_external_scheduling.py)
and [scheduling.md](scheduling.md).

## Pattern D — Dependency-aware refresh

```
Resource graph (Resource.dependencies) -> DependencyGraph -> change -> ImpactAnalyzer -> ImpactSet -> RefreshPlanner -> ordered RefreshPlan
```

Use when a change in one resource must propagate to resources that depend on
it (shared configuration, computed/derived resources, transitive
references). See
[`examples/02_dependencies_and_fingerprints.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/02_dependencies_and_fingerprints.py)
and [dependency-refresh.md](dependency-refresh.md).

## Pattern E — Consumer-specific adapter (generic resource integration)

The engine has no notion of your resource domain. A minimal, fully generic
adapter shape, adapted from
[`examples/13_abc_extensions.py`](https://github.com/smuniharish/refresh-engine/blob/main/examples/13_abc_extensions.py):

```python
from collections.abc import AsyncIterator
from refresh_engine import (
    DiscoveryResult, PlanAction, RefreshOperationABC, Resource,
    ResourceSnapshot, ResourceSourceABC,
)

class MySystemSource(ResourceSourceABC):
    """Translate *your* domain's list/read operations into Resource/ResourceSnapshot."""

    def __init__(self, client) -> None:
        self._client = client

    async def discover(self) -> DiscoveryResult:
        async def resources() -> AsyncIterator[Resource]:
            async for item in self._client.list_items():
                yield Resource(
                    item.id,
                    dependencies=frozenset(item.depends_on),
                    cheap_indicator=item.etag,
                    cheap_indicator_reliable=True,  # only if item.etag truly proves no change
                )

        return DiscoveryResult(resources(), complete=True)

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        item = await self._client.get_item(resource.resource_id)
        return ResourceSnapshot(
            resource.resource_id,
            content=item.body,
            etag=item.etag,
            dependencies=resource.dependencies,
        )


class MySystemOperation(RefreshOperationABC):
    """Translate REFRESH/DELETE plan actions into your domain's write/delete calls."""

    def __init__(self, destination) -> None:
        self._destination = destination

    async def __call__(self, resource, snapshot, action, request) -> None:
        if action is PlanAction.DELETE:
            assert resource is not None
            await self._destination.remove(resource.resource_id)
            return
        assert resource is not None and snapshot is not None
        await self._destination.upsert(resource.resource_id, snapshot.content)
```

Keep this adapter as the only place your domain's concepts (MCP tools, agent
skills, catalog entries, whatever they are) touch `refresh_engine` types. Do
not leak `Resource`/`ResourceSnapshot` construction logic into unrelated
application layers, and do not build a parallel, domain-specific
refresh/scheduling/hashing framework alongside this one — extend via the
documented protocols/ABCs instead (custom `FingerprintStrategy`,
`ResourceSelector`, `StateStore`, etc.).

Patterns compose — a real integration is usually B + C (incremental +
scheduled), or B + C + D (add dependency-awareness), wrapped in one E-style
adapter pair. There is no pattern that requires bypassing `RefreshEngine` or
reimplementing its stages.

## Testing

`pyproject.toml` runs pytest with `asyncio_mode = "auto"` (`pytest-asyncio`),
so `async def test_...` functions work without extra markers.

### Reusable fixture pattern (`tests/helpers.py`)

The package's own test suite uses two small, reusable, in-memory test doubles
instead of mocks — copy this pattern for your own integration tests:

```python
from refresh_engine import DiscoveryResult, PlanAction, Resource, ResourceSnapshot

class MutableSource:
    """Configurable ResourceSource: values, dependencies, complete flag,
    fail_discovery, fail_snapshot (set of IDs), reliable_indicators."""
    ...

class RecordingOperation:
    """Records (resource_id, action) calls; can be told to raise for
    specific resource IDs via `failures: set[str]`."""
    ...
```

Both are plain dataclasses satisfying the `ResourceSource`/`RefreshOperation`
protocols structurally — no mocking framework required.

### What to test (mirrors the package's own integration suite)

| Behavior to verify | Existing evidence |
| --- | --- |
| Initial refresh treats every resource as `ADDED` | [tests/integration/test_incremental.py](https://github.com/smuniharish/refresh-engine/blob/main/tests/integration/test_incremental.py) |
| Unchanged resources are skipped, even at scale (10,000 resources) | `test_ten_thousand_unchanged_resources_are_skipped` |
| Only changed resources are refreshed | `test_only_one_changed_resource_is_refreshed` |
| Added and deleted resources in the same refresh | `test_added_and_deleted_resources` |
| Dependency-aware ordering (dependents run after dependencies) | `test_dependency_aware_refresh_uses_dependency_order` |
| Failure does not advance fingerprint/state | `test_failed_refresh_does_not_advance_fingerprint` |
| Full and targeted mode selection | `test_full_and_targeted_modes_select_expected_resources` |
| Source failure never infers deletion | [tests/integration/test_safety.py](https://github.com/smuniharish/refresh-engine/blob/main/tests/integration/test_safety.py) → `test_source_failure_never_infers_deletion` |
| Partial discovery never infers deletion | `test_partial_discovery_never_infers_deletion` |
| Snapshot failure preserves prior state | `test_snapshot_failure_preserves_prior_state` |
| Scheduler start/stop/pause/resume lifecycle | [tests/scheduler/test_scheduler.py](https://github.com/smuniharish/refresh-engine/blob/main/tests/scheduler/test_scheduler.py) |
| Bounded concurrency and overlap policy | [tests/concurrency/test_execution.py](https://github.com/smuniharish/refresh-engine/blob/main/tests/concurrency/test_execution.py) |
| Cancellation leaves the store unaffected | `examples/11_cancellation_and_shutdown.py` |

Assert on `RefreshResult`, not just side effects — prefer:

```python
result = await engine.refresh()
assert result.status is RefreshStatus.SUCCESS
assert result.modified_count == 1
assert result.refreshed_count == 1
```

over only inspecting your operation's recorded calls: the counts are the
verified public contract and catch planning/detection regressions that a
side-effect-only assertion would miss. For state persistence, also assert on
`await engine.store.load_all()` directly.

### Running the package's own test suite (for reference behavior)

```bash
uv run pytest --cov=refresh_engine --cov-report=term-missing
```

`pyproject.toml` enforces `fail_under = 85` branch coverage. Use
`uv run python scripts/run_examples.py` to execute every example script as a
smoke test, and `uv run ruff check .` / `uv run pyrefly check` for lint and
type checking.
