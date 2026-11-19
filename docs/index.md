---
hide:
  - navigation
---

# refresh-engine

**Keep a destination in sync with a changing collection of resources, and only do
the work that changed.**

refresh-engine is an asyncio-first Python library. It discovers your resources,
detects what changed since the last successful refresh, plans the work in
dependency order, and runs your refresh operation with bounded concurrency,
retries, and transactional state. It knows nothing about your domain: you describe
how to list and read resources and what to do with them, and the engine takes care
of everything in between.

![The refresh pipeline](assets/diagrams/pipeline-light.png#only-light)
![The refresh pipeline](assets/diagrams/pipeline-dark.png#only-dark)

<div class="grid cards" markdown>

-   :material-lightning-bolt:{ .lg .middle } **Incremental by default**

    ---

    Deterministic fingerprints skip every resource that did not change, and with
    reliable cheap indicators a refresh of 100,000 unchanged resources reads none
    of them.

-   :material-graph-outline:{ .lg .middle } **Dependency-aware**

    ---

    Changes propagate to dependent resources, operations run dependencies first,
    failures skip only the affected dependents, and cycles are isolated.

-   :material-shield-check-outline:{ .lg .middle } **Safe by design**

    ---

    A failed or partial discovery never deletes anything, failed operations never
    advance state, and resources that need another attempt are retried
    automatically.

-   :material-tune-variant:{ .lg .middle } **Production controls**

    ---

    Bounded concurrency, retries with backoff, per-phase timeouts, overlap
    policies, scheduling, graceful cancellation, structured logs, and metrics.

</div>

## Install

```bash
pip install refresh-engine
```

refresh-engine requires Python 3.12 or newer.

## A first refresh

Implement a source that lists and reads resources, and an operation that applies
each change. The engine does the rest:

<!-- test -->
```python
import asyncio

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    ResourceSource,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)

CATALOG = {"apples": 120, "pears": 80}
SEARCH_INDEX: dict[str, int] = {}


class InventorySource(ResourceSource):
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

Running the program prints:

```text
success: refreshed 2 {'apples': 120, 'pears': 80}
success: modified 1, deleted 1 {'pears': 75}
```

The second refresh touches only the modified and the deleted resource. The
[examples](examples.md) show every feature in action.

## Downloads

<p>
  <a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520sum%2528count%2529%2520v%2520FROM%2520pypi.pypi_downloads_per_day%2520WHERE%2520project%253D%2527refresh-engine%2527%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=downloads&amp;color=blue&amp;cacheSeconds=3600" alt="Total downloads"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520sum%2528count%2529%2520v%2520FROM%2520pypi.pypi_downloads_per_day%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520date%2520%253E%253D%2520toStartOfWeek%2528today%2528%2529%252C%25201%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=this%20week&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads this week"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520sum%2528count%2529%2520v%2520FROM%2520pypi.pypi_downloads_per_day%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520date%2520%253E%253D%2520toStartOfMonth%2528today%2528%2529%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=this%20month&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads this month"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520sum%2528count%2529%2520v%2520FROM%2520pypi.pypi_downloads_per_day%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520date%2520%253E%253D%2520toStartOfYear%2528today%2528%2529%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=this%20year&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads this year"></a>
</p>
<p>
  <a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528toString%2528y%2529%252C%2520%2527%253A%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520toYear%2528date%2529%2520y%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day%2520WHERE%2520project%253D%2527refresh-engine%2527%2520GROUP%2520BY%2520y%2520ORDER%2520BY%2520y%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=by%20year&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by year"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527A%2527%2520AND%2520%2527A%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20A&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: A"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527B%2527%2520AND%2520%2527B%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20B&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: B"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527C%2527%2520AND%2520%2527C%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20C&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: C"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527D%2527%2520AND%2520%2527D%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20D&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: D"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527E%2527%2520AND%2520%2527E%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20E&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: E"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527F%2527%2520AND%2520%2527F%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20F&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: F"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527G%2527%2520AND%2520%2527G%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20G&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: G"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527H%2527%2520AND%2520%2527H%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20H&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: H"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527I%2527%2520AND%2520%2527I%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20I&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: I"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527J%2527%2520AND%2520%2527J%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20J&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: J"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527K%2527%2520AND%2520%2527K%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20K&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: K"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527L%2527%2520AND%2520%2527L%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20L&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: L"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527M%2527%2520AND%2520%2527M%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20M&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: M"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527N%2527%2520AND%2520%2527O%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20N-O&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: N-O"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527P%2527%2520AND%2520%2527P%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20P&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: P"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527R%2527%2520AND%2520%2527R%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20R&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: R"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527S%2527%2520AND%2520%2527S%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20S&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: S"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527T%2527%2520AND%2520%2527T%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20T&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: T"></a>
<a href="https://clickpy.clickhouse.com/dashboard/refresh-engine"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2520arrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2520%2527%2520%2527%252C%2520toString%2528c%2529%2529%2529%252C%2520%2527%2520%257C%2520%2527%2529%2520v%2520FROM%2520%2528SELECT%2520country_code%252C%2520sum%2528count%2529%2520c%2520FROM%2520pypi.pypi_downloads_per_day_by_version_by_country%2520WHERE%2520project%253D%2527refresh-engine%2527%2520AND%2520substring%2528country_code%252C%25201%252C%25201%2529%2520BETWEEN%2520%2527U%2527%2520AND%2520%2527Y%2527%2520GROUP%2520BY%2520country_code%2520HAVING%2520sum%2528count%2529%2520%253E%25200%2520ORDER%2520BY%2520c%2520DESC%252C%2520country_code%2529%2520FORMAT%2520JSON&amp;query=%24.data%5B0%5D.v&amp;label=countries%20U-Y&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country: U-Y"></a>
</p>

Counts come from the [ClickHouse public PyPI dataset](https://clickpy.clickhouse.com/dashboard/refresh-engine),
which refreshes daily, so they may be about a day behind. Shields caches badge
images for up to an hour, and GitHub may cache them longer. Weeks start on Monday
in UTC. Explore interactive charts on the
[ClickPy dashboard](https://clickpy.clickhouse.com/dashboard/refresh-engine).

## Where to go next

- [Getting started](getting-started.md) builds a complete integration step by step.
- [Concepts](concepts.md) explains resources, fingerprints, state, and statuses.
- The [guides](guides/index.md) cover each capability in depth.
- The [API reference](api/index.md) documents every public name.
