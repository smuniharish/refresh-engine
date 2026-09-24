"""Inheritance-based extensions using the optional abstract base class layer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    RefreshOperationABC,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
    ResourceSourceABC,
)


class Source(ResourceSourceABC):
    async def discover(self) -> DiscoveryResult:
        async def resources() -> AsyncIterator[Resource]:
            yield Resource("configuration")

        return DiscoveryResult(resources())

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        return ResourceSnapshot(resource.resource_id, content={"enabled": True})


class Operation(RefreshOperationABC):
    async def __call__(
        self,
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        assert resource is not None
        print(action.value, resource.resource_id)


async def main() -> None:
    engine = RefreshEngine(Source(), Operation())
    assert (await engine.refresh()).refreshed_count == 1
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
