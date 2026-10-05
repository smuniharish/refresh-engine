# refresh-engine

[![PyPI](https://img.shields.io/pypi/v/refresh-engine)](https://pypi.org/project/refresh-engine/)
[![Python](https://img.shields.io/pypi/pyversions/refresh-engine)](https://pypi.org/project/refresh-engine/)
[![CI](https://github.com/smuniharish/refresh-engine/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/smuniharish/refresh-engine/actions/workflows/ci.yml)
[![Documentation](https://readthedocs.org/projects/refresh-engine/badge/?version=latest)](https://refresh-engine.readthedocs.io/en/latest/)
[![License](https://img.shields.io/pypi/l/refresh-engine)](https://github.com/smuniharish/refresh-engine/blob/HEAD/LICENSE)

**Keep anything in sync with its source by refreshing only what changed.**

refresh-engine is an asyncio-first, domain-neutral Python library for keeping
derived data current: search indexes, caches, embeddings, configuration,
registries, or anything else built from a source. You describe how to list and
read resources and how to apply a change; the engine detects what changed,
plans the work in dependency order, runs it safely, and remembers the outcome.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/smuniharish/refresh-engine/HEAD/docs/assets/diagrams/pipeline-dark.png">
  <img alt="Discover, observe, detect changes, plan, execute, and persist" src="https://raw.githubusercontent.com/smuniharish/refresh-engine/HEAD/docs/assets/diagrams/pipeline-light.png">
</picture>

## Features

- **Precise change detection.** Deterministic fingerprints detect added,
  modified, and deleted resources. Reliable cheap indicators, such as ETags or
  version tokens, skip reading unchanged resources entirely.
- **Four refresh modes.** Incremental, full, dependency-aware, and targeted
  refreshes, with targets chosen by ID or by composable selectors.
- **Dependency ordering.** Dependencies are refreshed before their dependents,
  changes propagate to everything that depends on them, and cycles are isolated.
- **Safe by default.** Bounded concurrency, retries with jittered exponential
  backoff, timeouts, and fail-fast or best-effort policies. A partial discovery
  never deletes anything, and failed work is retried by the next refresh.
- **Durable state.** State advances only for successful resources, in one atomic
  commit per refresh. Use the in-memory store or plug in your own database.
- **Scheduling and overlap control.** A drift-free interval scheduler, a
  thread-safe bridge for external schedulers, and policies that coalesce, queue,
  skip, or supersede overlapping requests.
- **Observability.** Typed lifecycle events with correlation IDs, structured
  logging through [structlog](https://www.structlog.org) with secret redaction,
  and metrics.
- **Lightweight and typed.** Fully type-annotated, with two runtime
  dependencies: structlog and [tenacity](https://tenacity.readthedocs.io).

## Installation

refresh-engine requires Python 3.12 or newer.

```bash
pip install refresh-engine
```

## Quick start

Implement a source that lists and reads resources, and an operation that applies
each change. The engine does the rest:

<!-- test -->
```python
import asyncio

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)

CATALOG = {"apples": 120, "pears": 80}
SEARCH_INDEX: dict[str, int] = {}


class InventorySource:
    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(Resource(item) for item in sorted(CATALOG))

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource.resource_id, content=CATALOG[resource.resource_id]
        )


async def update_index(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    if action is PlanAction.DELETE:
        SEARCH_INDEX.pop(resource.resource_id, None)
    elif snapshot is not None:
        SEARCH_INDEX[resource.resource_id] = snapshot.content


async def main() -> None:
    async with RefreshEngine(InventorySource(), update_index) as engine:
        first = await engine.refresh()
        print(f"{first.status}: refreshed {first.refreshed_count}", SEARCH_INDEX)

        CATALOG["pears"] = 75
        del CATALOG["apples"]
        second = await engine.refresh()
        print(
            f"{second.status}: modified {second.modified_count},"
            f" deleted {second.deleted_count}",
            SEARCH_INDEX,
        )


asyncio.run(main())
```

```text
success: refreshed 2 {'apples': 120, 'pears': 80}
success: modified 1, deleted 1 {'pears': 75}
```

The second refresh touches only the modified and the deleted resource. Continue
with the [getting started guide](https://refresh-engine.readthedocs.io/en/latest/getting-started/)
to add durable state, dependencies, scheduling, and logging.

## Performance

The engine's overhead is small next to real I/O: with in-memory sources, a full
refresh of 100,000 resources takes about 3 seconds, and an incremental refresh
that finds nothing changed takes about half a second. See the
[benchmarks](https://github.com/smuniharish/refresh-engine/blob/HEAD/benchmarks/README.md)
for the method and details.

## Documentation

The [documentation](https://refresh-engine.readthedocs.io/en/latest/) covers:

- [Getting started](https://refresh-engine.readthedocs.io/en/latest/getting-started/)
  and [concepts](https://refresh-engine.readthedocs.io/en/latest/concepts/)
- [Guides](https://refresh-engine.readthedocs.io/en/latest/guides/) for every
  feature, including a
  [production checklist](https://refresh-engine.readthedocs.io/en/latest/guides/production/)
- [Runnable examples](https://refresh-engine.readthedocs.io/en/latest/examples/),
  including SQLite and PostgreSQL state stores
- The [API reference](https://refresh-engine.readthedocs.io/en/latest/api/)
- The [architecture](https://refresh-engine.readthedocs.io/en/latest/architecture/overview/)
  and its guarantees

## Agent Skills

Coding agents such as Claude Code, Codex, Cursor, and GitHub Copilot can use the
refresh-engine [Agent Skill](https://refresh-engine.readthedocs.io/en/latest/agent-skills/)
to integrate the library correctly:

```bash
npx skills add https://github.com/smuniharish/refresh-engine/tree/master/refresh-engine-skills/skills/refresh-engine
```

## Contributing

Contributions are welcome. Read the
[contributing guide](https://github.com/smuniharish/refresh-engine/blob/HEAD/CONTRIBUTING.md),
and report vulnerabilities as described in the
[security policy](https://github.com/smuniharish/refresh-engine/blob/HEAD/SECURITY.md).
Notable changes are listed in the
[changelog](https://github.com/smuniharish/refresh-engine/blob/HEAD/CHANGELOG.md).

## License

refresh-engine is licensed under the
[Apache License 2.0](https://github.com/smuniharish/refresh-engine/blob/HEAD/LICENSE).
