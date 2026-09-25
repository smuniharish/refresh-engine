# Example: basic and modes

Source: `examples/01_basic_and_modes.py` (runnable as-is with
`refresh-engine==0.1.0`, Python 3.12). Uses the shared `examples/common.py`
fixtures: `MemorySource` and `Recorder`.

```python
"""Basic, incremental, full, and targeted refresh scenarios."""
import asyncio

from common import MemorySource, Recorder

from refresh_engine import RefreshEngine, RefreshMode


async def main() -> None:
    source = MemorySource({"alpha": 1, "beta": 2})
    recorder = Recorder()
    engine = RefreshEngine(source, recorder)

    first = await engine.refresh()
    unchanged = await engine.refresh()
    source.values["alpha"] = 3
    incremental = await engine.refresh()
    full = await engine.refresh(mode=RefreshMode.FULL)
    targeted = await engine.refresh(mode=RefreshMode.TARGETED, resource_ids={"beta"})

    print(f"first: refreshed={first.refreshed_count}")
    print(
        f"unchanged: unchanged={unchanged.unchanged_count}, "
        f"refreshed={unchanged.refreshed_count}"
    )
    print(
        f"incremental: modified={incremental.modified_count}, "
        f"refreshed={incremental.refreshed_count}"
    )
    print(f"full: refreshed={full.refreshed_count}")
    print(f"targeted: refreshed={targeted.refreshed_count}")
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
```

## Verified output

```text
first: refreshed=2
unchanged: unchanged=2, refreshed=0
incremental: modified=1, refreshed=1
full: refreshed=2
targeted: refreshed=1
```

## What this demonstrates

- `RefreshEngine(source, operation)` needs only your `ResourceSource` and
  `RefreshOperation` — every other constructor argument has a working default.
- Calling `engine.refresh()` with no arguments defaults to
  `RefreshMode.INCREMENTAL`.
- A no-op second refresh proves unchanged resources are not re-processed.
- `RefreshMode.FULL` re-runs every discovered resource regardless of change.
- `RefreshMode.TARGETED` requires `resource_ids` and only processes those IDs.
- Always call `await engine.close()` when done with an engine.

## Minimal `ResourceSource`/`RefreshOperation` without the shared fixtures

If you don't want to reuse `examples/common.py`, the smallest possible
integration (from the project README, also verified to run) is:

```python
import asyncio
from refresh_engine import (
    DiscoveryResult, PlanAction, RefreshEngine, Resource, ResourceSnapshot,
)

class Source:
    async def discover(self):
        async def resources():
            yield Resource("alpha")
        return DiscoveryResult(resources())

    async def snapshot(self, resource):
        return ResourceSnapshot(resource.resource_id, content={"value": 1})

async def apply(resource, snapshot, action, request):
    assert resource is not None
    print(action, resource.resource_id)

async def main():
    engine = RefreshEngine(Source(), apply)
    result = await engine.refresh()
    print(result.status, result.refreshed_count)
    await engine.close()

asyncio.run(main())
```

Note: for a `PlanAction.DELETE`, the operation receives a lightweight
`Resource` carrying only the deleted ID, with `snapshot=None` — deletion
handling must not expect source payloads.
