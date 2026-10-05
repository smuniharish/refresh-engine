"""Ten thousand resources: only what changed is read and refreshed."""

import asyncio

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


class Catalog:
    """Products with a revision number that changes on every edit."""

    def __init__(self, size: int) -> None:
        self.revisions = dict.fromkeys(range(size), 0)
        self.reads = 0

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(
            Resource(
                f"product-{index}",
                cheap_indicator=str(revision),
                cheap_indicator_reliable=True,
            )
            for index, revision in self.revisions.items()
        )

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        self.reads += 1
        index = int(resource.resource_id.removeprefix("product-"))
        return ResourceSnapshot(resource.resource_id, content=self.revisions[index])


async def publish(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    return None


async def main() -> None:
    catalog = Catalog(10_000)
    async with RefreshEngine(catalog, publish) as engine:
        initial = await engine.refresh()
        print(f"initial: refreshed={initial.refreshed_count}, reads={catalog.reads}")

        catalog.reads = 0
        for index in range(0, 10_000, 1_000):
            catalog.revisions[index] += 1
        update = await engine.refresh()
        print(
            f"update:  refreshed={update.refreshed_count}, reads={catalog.reads}, "
            f"unchanged={update.unchanged_count}"
        )


if __name__ == "__main__":
    asyncio.run(main())
