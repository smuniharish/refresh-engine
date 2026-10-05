"""Dependency ordering, failure isolation, deletions, and dependency cycles."""

import asyncio

from common import DictionarySource

from refresh_engine import (
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshMode,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


class Publisher:
    """Publish resources, failing for IDs listed in `broken`."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.broken: set[str] = set()

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        self.calls.append(f"{action.value} {resource.resource_id}")
        if resource.resource_id in self.broken:
            raise ConnectionError("destination unavailable")

    def take(self) -> str:
        calls = ", ".join(self.calls) or "none"
        self.calls.clear()
        return calls


async def main() -> None:
    source = DictionarySource(
        {"schema": 1, "tables": 1, "views": 1, "reports": 1},
        dependencies={
            "tables": {"schema"},
            "views": {"tables"},
            "reports": {"views"},
        },
    )
    publisher = Publisher()
    config = RefreshConfig(max_concurrency=4)
    async with RefreshEngine(source, publisher, config=config) as engine:
        first = await engine.refresh()
        print(f"dependency order: {publisher.take()} -> {first.status}")

        source.contents["schema"] = 2
        publisher.broken = {"tables"}
        failed = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
        print(f"tables fails:     {publisher.take()} -> {failed.status}")
        for issue in failed.errors:
            print(f"                  {issue.resource_id}: {issue.message}")

        publisher.broken.clear()
        retried = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
        print(f"retry:            {publisher.take()} -> {retried.status}")

        del source.contents["reports"]
        deleted = await engine.refresh()
        print(f"deletion:         {publisher.take()} -> {deleted.status}")

        source.dependencies["schema"] = {"views"}
        source.contents["schema"] = 3
        cyclic = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
        print(f"cycle:            {publisher.take()} -> {cyclic.status}")
        print(f"                  {cyclic.errors[0].message}")


if __name__ == "__main__":
    asyncio.run(main())
