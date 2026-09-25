# Incremental refresh and refresh modes

Source: `docs/guides/refresh-modes.md`, `src/refresh_engine/planning/planner.py`,
`examples/01_basic_and_modes.py` (verified output below).

## The four modes (`RefreshMode`)

| Mode | Resources selected for execution | Typical use |
| --- | --- | --- |
| `INCREMENTAL` (default) | `ADDED`, `MODIFIED`, and safely inferred `DELETED` resources | Steady-state periodic refresh |
| `FULL` | Every currently discovered resource, plus safe deletions | Backfill, cache rebuild, recovery from suspected drift |
| `TARGETED` | The explicit `resource_ids` you pass in, intersected with discovered changes | Refresh-on-demand for specific known IDs (webhooks, user action) |
| `DEPENDENCY_AWARE` | Directly changed resources **and** their transitive dependents | Changes must propagate (e.g. a shared config used by many resources) |

`RefreshRequest(mode=RefreshMode.TARGETED, ...)` **requires** a non-empty
`resource_ids`; constructing it without one raises `ValueError`.

## Why incremental refresh exists

At scale, re-running every resource's operation on every refresh does not scale:

```
10,000 resources
9,950 unchanged
   20 modified
   15 added
   10 deleted
    5 dependency-impacted
```

`INCREMENTAL` mode (and `DEPENDENCY_AWARE` for the last row) means the executor
only runs `RefreshOperation` for the ~50 resources that actually need it, not
all 10,000. This is achieved through two layers, cheapest first:

1. **Cheap pre-check** — if `Resource.cheap_indicator_reliable` is `True` and
   `Resource.cheap_indicator` equals the stored `ResourceState.cheap_indicator`,
   the engine skips `snapshot()` and fingerprinting entirely and reuses the
   previous fingerprint (see [fingerprinting.md](fingerprinting.md)).
2. **Strong fingerprint comparison** — otherwise the engine snapshots the
   resource, computes a fresh `ResourceFingerprint`, and compares it to the
   stored one via `ChangeDetector.detect()`.

```
cheap indicator matches stored state? --yes--> reuse previous fingerprint (no snapshot)
        | no / not reliable
        v
snapshot() -> fingerprint() -> compare to stored ResourceState.fingerprint
        |
        +-- no previous state           -> ChangeType.ADDED
        +-- fingerprint differs         -> ChangeType.MODIFIED
        +-- fingerprint same, deps differ -> ChangeType.DEPENDENCY_CHANGED
        +-- fingerprint same, deps same -> ChangeType.UNCHANGED
   (any previous ID missing from a *complete* discovery) -> ChangeType.DELETED
```

## Verified example output

`examples/01_basic_and_modes.py` (`MemorySource({"alpha": 1, "beta": 2})`):

```text
first: refreshed=2
unchanged: unchanged=2, refreshed=0
incremental: modified=1, refreshed=1
full: refreshed=2
targeted: refreshed=1
```

- The first refresh treats both resources as `ADDED` and refreshes both.
- A second refresh with no source changes reports both `unchanged` and
  refreshes zero resources — this is the payoff of incremental refresh.
- After mutating one value, an `INCREMENTAL` refresh reports one `modified`
  and refreshes exactly that one resource.
- `FULL` re-runs the operation for all discovered resources regardless of
  change.
- `TARGETED` with `resource_ids={"beta"}` refreshes only `beta`.

## Usage

```python
from refresh_engine import RefreshMode

full = await engine.refresh(mode=RefreshMode.FULL)
targeted = await engine.refresh(mode=RefreshMode.TARGETED, resource_ids={"resource-a", "resource-b"})
dependency_aware = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)

print(full.refreshed_count, targeted.refreshed_count, dependency_aware.impacted_count)
```

`RefreshResult` gives you exact counts (`added_count`, `modified_count`,
`deleted_count`, `unchanged_count`, `impacted_count`, `refreshed_count`,
`skipped_count`, `failed_count`) — use these in tests instead of asserting on
side effects alone.

## Choosing a mode: decision guidance

- Default to `INCREMENTAL`. It is correct for the overwhelming majority of
  "keep destination in sync" integrations.
- Use `FULL` sparingly (e.g. a manual "force resync" action, or recovering from
  a known state-store issue) — it does not skip unchanged resources.
- Use `TARGETED` when a specific external event names exact resource IDs
  (e.g. a webhook payload) — combine with `QueryTrigger` (see
  [scheduling.md](scheduling.md)).
- Use `DEPENDENCY_AWARE` when your model has real dependents that must be
  reprocessed after an upstream change — see
  [dependency-refresh.md](dependency-refresh.md). It still uses the same
  change-detection machinery as `INCREMENTAL`; it only changes *which*
  resources the planner selects (direct changes + `ImpactSet.all_impacted`).
