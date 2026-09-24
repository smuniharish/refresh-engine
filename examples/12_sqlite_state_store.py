"""Durable restart continuity with the standard-library SQLite state store."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from common import MemorySource, Recorder
from sqlite_store import SQLiteStateStore

from refresh_engine import RefreshEngine, StateStoreABC


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "refresh-state.db"
        source = MemorySource({"configuration": {"enabled": True}})
        store = SQLiteStateStore(database)
        assert isinstance(store, StateStoreABC)

        first = RefreshEngine(source, Recorder(), store=store)
        assert (await first.refresh()).refreshed_count == 1
        await first.close()

        second = RefreshEngine(source, Recorder(), store=SQLiteStateStore(database))
        result = await second.refresh()
        assert result.unchanged_count == 1
        assert result.refreshed_count == 0
        print(
            f"sqlite restart: unchanged={result.unchanged_count}, "
            f"refreshed={result.refreshed_count}"
        )
        await second.close()


if __name__ == "__main__":
    asyncio.run(main())
