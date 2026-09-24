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
