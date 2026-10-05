# Dependencies

## Declaring dependencies

A resource lists the IDs it depends on, in discovery, in its snapshot, or in both:

```python
Resource("report", dependencies={"sales", "regions"})
ResourceSnapshot("report", content=report, dependencies={"sales", "regions"})
```

- `None`, the default, means "not declared". A snapshot's dependencies take
  precedence over discovery's.
- When a resource is matched by its cheap indicator, its stored dependencies are
  kept unless discovery declares new ones.
- Dependencies on unknown IDs are allowed and have no effect.
- When only the dependencies of a resource change, it is classified as
  dependency-changed and refreshed by every mode.

## Propagating changes

`INCREMENTAL` refreshes only resources that changed themselves. Use
`RefreshMode.DEPENDENCY_AWARE` when a resource must be rebuilt whenever something it
depends on, directly or transitively, changes or is deleted:

```python
from refresh_engine import RefreshMode

result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
print(result.modified_count, result.impacted_count)
```

Impacted resources are refreshed even though they are unchanged, and are counted in
`impacted_count`. Only discovered resources receive operations.

## Ordering guarantees

- A resource's operation starts only after the operations of everything it depends
  on have finished.
- Deleted dependencies are removed before their dependents are refreshed, and
  dependents are deleted before the resources they depend on.
- Independent resources run concurrently, up to `max_concurrency`.

Do not order calls manually or run one refresh per dependency level; one refresh
orders everything.

## Failure isolation

When an operation fails, its direct and transitive dependents are skipped instead
of running against stale input. They appear in `result.executions` with
`attempts == 0` and an error such as `not run because dependency 'tables' failed`.
Unrelated resources continue. Skipped resources are retried automatically by the
next refresh, in any mode.

## Cycles

Resources in a dependency cycle fail with a `DependencyCycleError` issue in the
planning phase that names the cycle, such as
`dependency cycle detected: schema -> views -> tables -> schema`. Their dependents
are skipped, and everything else is refreshed. A resource that depends on itself is
a cycle. Break cycles in the source data; the engine cannot order them.
