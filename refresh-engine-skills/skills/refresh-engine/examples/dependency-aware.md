# Example: dependency-aware refresh

Source: `examples/02_dependencies_and_fingerprints.py` (runnable as-is).

```python
"""Dependency-aware planning and alternate fingerprint scenarios."""
import asyncio

from common import MemorySource, Recorder

from refresh_engine import ContentHash, RefreshEngine, RefreshMode


async def main() -> None:
    source = MemorySource(
        {"base": {"v": 1}, "derived": {"v": 1}},
        dependencies={"derived": {"base"}},
    )
    recorder = Recorder()
    engine = RefreshEngine(source, recorder, fingerprint=ContentHash())
    await engine.refresh()

    source.values["base"] = {"v": 2}
    result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
    refreshed_ids = [resource_id for _, resource_id in recorder.calls[-2:]]
    print(
        f"dependency-aware: modified={result.modified_count}, "
        f"impacted={result.impacted_count}, refreshed={refreshed_ids}"
    )
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
```

## Verified output

```text
dependency-aware: modified=1, impacted=1, refreshed=['base', 'derived']
```

## What this demonstrates

- `dependencies={"derived": {"base"}}` declares that `derived` depends on
  `base` — set via `Resource(..., dependencies=frozenset({"base"}))` in a
  real `ResourceSource.discover()`.
- Changing `base`'s content and refreshing with
  `RefreshMode.DEPENDENCY_AWARE` refreshes **both** `base` (`modified=1`,
  directly changed) and `derived` (`impacted=1`, indirectly impacted via
  `ImpactAnalyzer`).
- Execution order respects the dependency graph: `base` runs before
  `derived` (topological levels), visible in `recorder.calls` order.
- `fingerprint=ContentHash()` overrides the default `CompositeHash` — any
  `FingerprintStrategy` can be supplied at construction time.

## Building a larger dependency graph

```python
source = MemorySource(
    {"config": {...}, "service-a": {...}, "service-b": {...}},
    dependencies={
        "service-a": {"config"},
        "service-b": {"config"},
    },
)
```

A change to `config` impacts both `service-a` and `service-b` in one
`DEPENDENCY_AWARE` refresh; a cycle among these (e.g. `service-a` depending
on `service-b` depending on `service-a`) raises `DependencyCycleError` when
the engine tries to plan topological levels — fix the dependency data rather
than catching and ignoring this error.

See [../references/dependency-refresh.md](../references/dependency-refresh.md)
for the full impact-analysis and planning mechanics.
