"""Quick start: incremental refresh of a small resource population."""

import asyncio

from common import DictionarySource, Recorder

from refresh_engine import RefreshEngine


async def main() -> None:
    source = DictionarySource({"alpha": "v1", "beta": "v1"})
    operation = Recorder()
    async with RefreshEngine(source, operation) as engine:
        first = await engine.refresh()
        print(f"first:     {first.status}, refreshed={first.refreshed_count}")
        print(f"           operations: {operation.take()}")

        second = await engine.refresh()
        print(f"unchanged: {second.status}, skipped={second.skipped_count}")
        print(f"           operations: {operation.take()}")

        source.contents["alpha"] = "v2"
        source.contents["gamma"] = "v1"
        del source.contents["beta"]
        third = await engine.refresh()
        print(
            f"changed:   {third.status}, added={third.added_count}, "
            f"modified={third.modified_count}, deleted={third.deleted_count}"
        )
        print(f"           operations: {operation.take()}")


if __name__ == "__main__":
    asyncio.run(main())
