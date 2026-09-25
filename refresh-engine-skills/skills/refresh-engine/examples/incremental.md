# Example: incremental refresh at scale

Source: `examples/10_scale_and_genericity.py` (runnable as-is).

```python
"""Ten-thousand-resource refresh and domain-generic identity demonstration."""
import asyncio

from common import MemorySource, Recorder

from refresh_engine import RefreshEngine


async def main() -> None:
    values = {f"synthetic-{index}": index for index in range(10_000)}
    engine = RefreshEngine(MemorySource(values), Recorder())
    await engine.refresh()
    unchanged = await engine.refresh()
    assert unchanged.unchanged_count == 10_000
    assert unchanged.refreshed_count == 0
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
```

## Verified behavior

After the baseline refresh, a second `INCREMENTAL` refresh over 10,000
unchanged resources reports `unchanged_count == 10_000` and
`refreshed_count == 0` — the `RefreshOperation` is invoked **zero times**.
This is the core payoff of fingerprint-based incremental refresh: the engine
still discovers and fingerprints every resource (an `O(V)` pass), but only
resources whose fingerprint changed reach your operation.

If most of your resources also expose a cheap, provably-reliable indicator
(an ETag, version token, or `Last-Modified` header that cannot go stale),
setting `Resource.cheap_indicator` / `cheap_indicator_reliable=True` in your
`ResourceSource.discover()` skips even the `snapshot()` call for unchanged
resources — see [../references/fingerprinting.md](../references/fingerprinting.md).

## Mixed changes (added / modified / deleted / unchanged / dependency-changed)

Combining `examples/01_basic_and_modes.py` and
`examples/02_dependencies_and_fingerprints.py` semantics, a realistic
incremental refresh over a larger set reports every `ChangeType` via
`RefreshResult`:

```python
result = await engine.refresh()  # default: RefreshMode.INCREMENTAL
print(
    result.added_count,
    result.modified_count,
    result.deleted_count,
    result.unchanged_count,
    result.impacted_count,   # only non-zero in RefreshMode.DEPENDENCY_AWARE
    result.refreshed_count,
)
```

Use these counts directly in assertions and dashboards instead of counting
your own operation's call log — see
[../references/incremental-refresh.md](../references/incremental-refresh.md).

## Observing what actually ran, via events

`examples/07_events_and_observability.py` shows lifecycle events (13 distinct
`EventKind`s in a typical successful single-resource refresh) feeding into a
provider-neutral `InMemoryMetrics` sink through a subscriber, without the
engine emitting metrics itself:

```python
from refresh_engine import EventPublisher, RefreshEngine
from refresh_engine.observability.metrics import InMemoryMetrics

events = EventPublisher()
metrics = InMemoryMetrics()

def count_event(event) -> None:
    metrics.increment(f"event.{event.kind.value}")

events.subscribe(count_event)
engine = RefreshEngine(source, operation, events=events)
result = await engine.refresh()
await events.drain()
```

Verified output: `summary: status=success, event_kinds=13, handler_errors=0`.
