# Dependencies

Resources often depend on each other: a search index on the documents it indexes, a
report on the tables it summarizes, a bundle on its modules. refresh-engine tracks
these relationships so that changes propagate and operations run in a safe order.

## Declaring dependencies

A resource lists the IDs of the resources it depends on, either in discovery or in
its snapshot:

```python
Resource("report", dependencies={"sales", "regions"})
ResourceSnapshot("report", content=..., dependencies={"sales", "regions"})
```

- Leave `dependencies` as `None` when a source does not know them. A snapshot's
  dependencies take precedence over discovery's.
- When a resource is matched by its cheap indicator, its stored dependencies are
  kept unless discovery declares new ones.
- Dependencies on unknown IDs are allowed; they simply never trigger anything.

When only the dependencies of a resource change, it is classified as
`DEPENDENCY_CHANGED` and refreshed by incremental refreshes.

## Propagating changes

An `INCREMENTAL` refresh only refreshes resources that changed themselves. A
`DEPENDENCY_AWARE` refresh also refreshes every discovered resource that depends,
directly or transitively, on a changed or deleted resource. These resources are
reported in `impacted_count`.

```python
from refresh_engine import RefreshMode

result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
print(result.modified_count, result.impacted_count)
```

Impact flows through every known relationship, including resources that were not
discovered in this refresh, but only discovered resources receive an operation.

## Ordering and concurrency

Operations run with up to `max_concurrency` in parallel, but never before the
operations of the resources they depend on have finished. Deletions are ordered
too: a deleted dependency is removed before its dependents are refreshed, and
dependents are deleted before the resources they depend on.

## Failure isolation

When an operation fails, the operations of the resources that depend on it, directly
or transitively, are skipped instead of running against stale input. Unrelated
resources continue.

![A failed operation skips its dependents](../assets/diagrams/dependency-execution-light.png#only-light)
![A failed operation skips its dependents](../assets/diagrams/dependency-execution-dark.png#only-dark)

Skipped resources appear in `executions` with zero attempts and an issue such as
`not run because dependency 'tables' failed`. Because their state did not advance,
the next refresh retries them automatically, in any mode.

## Cycles

Dependency cycles cannot be ordered. The resources in a cycle fail with a
[`DependencyCycleError`][refresh_engine.DependencyCycleError] issue that shows the
cycle, for example `dependency cycle detected: schema -> views -> tables -> schema`,
and their dependents are skipped. Every other resource is refreshed normally. A
resource that depends on itself is a cycle of one.

## Example

```python
--8<--
examples/03_dependencies.py
--8<--
```

```text
--8<--
examples/output/03_dependencies.txt
--8<--
```
