"""Full, targeted, selector-based, and dependency-aware refresh modes."""

import asyncio

from common import DictionarySource, Recorder

from refresh_engine import IDSelector, RefreshEngine, RefreshMode, TagSelector


async def main() -> None:
    source = DictionarySource(
        {"catalog": 1, "pricing": 1, "search-index": 1, "banner": 1},
        dependencies={"search-index": {"catalog", "pricing"}},
        tags={"pricing": ["finance"], "banner": ["marketing"]},
    )
    operation = Recorder()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.take()

        full = await engine.refresh(mode=RefreshMode.FULL)
        print(f"full:             refreshed={full.refreshed_count}")
        print(f"                  operations: {operation.take()}")

        targeted = await engine.refresh(resource_ids=["banner"])
        print(f"targeted by ID:   refreshed={targeted.refreshed_count}")
        print(f"                  operations: {operation.take()}")

        selector = TagSelector(["finance", "marketing"], match_all=False)
        tagged = await engine.refresh(selector=selector & ~IDSelector("banner"))
        print(f"targeted by tag:  refreshed={tagged.refreshed_count}")
        print(f"                  operations: {operation.take()}")

        source.contents["catalog"] = 2
        incremental = await engine.refresh(mode=RefreshMode.INCREMENTAL)
        print(f"incremental:      refreshed={incremental.refreshed_count}")
        print(f"                  operations: {operation.take()}")

        source.contents["pricing"] = 2
        aware = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
        print(
            f"dependency-aware: refreshed={aware.refreshed_count}, "
            f"impacted={aware.impacted_count}"
        )
        print(f"                  operations: {operation.take()}")


if __name__ == "__main__":
    asyncio.run(main())
