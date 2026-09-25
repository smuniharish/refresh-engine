# Testing guidance

Source: `tests/helpers.py`, `tests/integration/test_incremental.py`,
`tests/integration/test_safety.py`, `tests/scheduler/test_scheduler.py`,
`tests/concurrency/test_execution.py`. `pyproject.toml` runs pytest with
`asyncio_mode = "auto"` (`pytest-asyncio`), so `async def test_...` functions
work without extra markers.

## Reusable fixtures pattern (`tests/helpers.py`)

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

## What to test (mirrors the package's own integration suite)

- **Initial refresh** — all resources are `ADDED` and refreshed.
- **Unchanged resources are skipped** — a second refresh with no source
  changes reports `unchanged_count == N` and `refreshed_count == 0`
  (`test_ten_thousand_unchanged_resources_are_skipped`, verifying incremental
  refresh scales — only changed resources trigger work even with 10,000
  resources present).
- **Only changed resources are refreshed**
  (`test_only_one_changed_resource_is_refreshed`).
- **Added and deleted resources** in the same refresh
  (`test_added_and_deleted_resources`).
- **Dependency-aware ordering** — dependents run after their dependencies
  (`test_dependency_aware_refresh_uses_dependency_order`).
- **Failure does not advance fingerprint/state**
  (`test_failed_refresh_does_not_advance_fingerprint`) — assert the *next*
  refresh still reports the resource as changed, and/or assert directly on
  `await engine.store.load_all()`.
- **Full and targeted mode selection**
  (`test_full_and_targeted_modes_select_expected_resources`).
- **Source failure never infers deletion**
  (`test_source_failure_never_infers_deletion`) — `fail_discovery=True` must
  raise/produce `RefreshStatus.FAILED`, never an empty successful discovery.
- **Partial discovery never infers deletion**
  (`test_partial_discovery_never_infers_deletion`) — `complete=False` plus a
  removed resource ID must leave that resource's state untouched.
- **Snapshot failure preserves prior state**
  (`test_snapshot_failure_preserves_prior_state`) — `fail_snapshot={"id"}`.
- **Scheduler start/stop/pause/resume lifecycle**
  (`tests/scheduler/test_scheduler.py`) — assert `SchedulerError` on invalid
  transitions, and that `stop()` awaits in-flight work per
  `cancel_active_on_stop`.
- **Bounded concurrency and overlap policy**
  (`tests/concurrency/test_execution.py`) — assert no more than
  `max_concurrency` operations run concurrently (e.g. track a live-call
  counter inside your `RecordingOperation`), and assert each `OverlapPolicy`'s
  behavior (`SKIP_IF_RUNNING` returns `RefreshStatus.SKIPPED`; `COALESCE`
  merges into one run; `CANCEL_PREVIOUS` cancels the prior run).
- **Cancellation** — cancel an in-flight `engine.refresh()` task and assert
  `await engine.store.load_all()` is unaffected by the interrupted resource
  (see `examples/11_cancellation_and_shutdown.py`).

## Assert on `RefreshResult`, not just side effects

Prefer:

```python
result = await engine.refresh()
assert result.status is RefreshStatus.SUCCESS
assert result.modified_count == 1
assert result.refreshed_count == 1
```

over only inspecting your operation's recorded calls — the counts are the
verified public contract and catch planning/detection regressions that a
side-effect-only assertion would miss.

## Running the package's own test suite (for reference behavior)

```bash
uv run pytest --cov=refresh_engine --cov-report=term-missing
```

`pyproject.toml` enforces `fail_under = 85` branch coverage. Use
`uv run python scripts/run_examples.py` to execute every example script as a
smoke test, and `uv run ruff check .` / `uv run pyrefly check` for lint and
type checking.
