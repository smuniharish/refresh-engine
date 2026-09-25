# Dependency-aware refresh

Source: `src/refresh_engine/planning/dependency.py`,
`src/refresh_engine/planning/planner.py`,
`examples/02_dependencies_and_fingerprints.py` (verified output below).

## Model

Dependencies are plain resource IDs carried on `Resource.dependencies` and
`ResourceSnapshot.dependencies` (both `frozenset[str]`). `DependencyGraph`
stores edges as "resource depends on dependency" (`add_dependency(resource_id,
dependency_id)` adds a directed edge `dependency_id -> resource_id` internally,
i.e. dependents are reachable from dependencies via `networkx` successors).

```
base --> derived        # derived depends on base
```

If `base` changes, `derived` is impacted.

## Impact analysis

```
ChangeSet (from ChangeDetector)
   -> direct = { resource_id : change_type is not UNCHANGED }
   -> ImpactAnalyzer.analyze(changes, graph)
        -> impacted = graph.transitive_dependents(direct)   # includes direct itself
        -> ImpactSet(
              directly_changed = direct,
              indirectly_impacted = impacted - direct,
              unaffected = graph.nodes - impacted,
           )
```

`ImpactSet.all_impacted` is the union of `directly_changed` and
`indirectly_impacted` — this is exactly the resource-ID set that
`RefreshMode.DEPENDENCY_AWARE` selects for execution (see
`RefreshPlanner.plan`, which sets `selected = set(impact.all_impacted)` for
that mode).

## Planning and execution order

`RefreshPlanner.plan()` computes `graph.topological_levels(refreshable)` —
resources with no unresolved dependency come first, and each subsequent level
depends only on resources in prior levels. `RefreshExecutor.execute()` runs
each level's items concurrently (bounded by `max_concurrency`) but always
finishes one level before starting the next, guaranteeing dependencies
complete their operation before dependents run theirs.

A cycle in the dependency graph is a hard error: `topological_levels()` raises
`DependencyCycleError(cycle)` with the offending cycle as a tuple of resource
IDs. **Dependency cycles are never silently broken** — fix the source data or
your dependency modeling.

Missing dependency IDs (a resource declares a dependency on an ID that is
never discovered) are treated as external leaves; the graph simply has no
node for them. Decide in your `ResourceSource` whether an undiscoverable
dependency is valid for your domain (e.g. a deliberately external reference)
or should be surfaced as a data-quality problem.

## Verified example output

`examples/02_dependencies_and_fingerprints.py`: `derived` depends on `base`;
after changing `base`'s content and running `RefreshMode.DEPENDENCY_AWARE`:

```text
dependency-aware: modified=1, impacted=1, refreshed=['base', 'derived']
```

`modified=1` is `base` (`ChangeType.MODIFIED`); `impacted=1` is `derived` in
`indirectly_impacted`; both `base` and `derived` are refreshed, and `base` runs
before `derived` because of topological ordering.

## Usage

```python
from refresh_engine import ContentHash, RefreshEngine, RefreshMode

engine = RefreshEngine(source, operation, fingerprint=ContentHash())
await engine.refresh()  # establish baseline state

# ... base resource content changes upstream ...

result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
print(result.modified_count, result.impacted_count)
```

Dependencies come from your `ResourceSource.discover()`/`snapshot()`
implementation — set `Resource(..., dependencies=frozenset({"base"}))` (or on
`ResourceSnapshot`) for each dependent resource; the engine builds the graph
for you from `previous` and `current` `ResourceState.dependencies` on every
refresh (see `RefreshEngine._build_graph`).

## When dependency-aware refresh is NOT what you want

- If dependents should simply be re-evaluated on their own change schedule
  (eventually consistent, not immediately on the dependency's change),
  `INCREMENTAL` mode alone may be sufficient — don't add dependency tracking
  you don't need.
- If "dependency" in your domain really means "this resource must be created
  before that one" for a one-time bulk load rather than an ongoing refresh
  relationship, a dependency graph is still the right tool, but you may not
  need repeated `DEPENDENCY_AWARE` refreshes after the initial load.
