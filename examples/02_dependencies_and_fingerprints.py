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
