"""Real PostgreSQL StateStore injection and restart-continuity smoke test."""

from __future__ import annotations

import asyncio
import os
import selectors
import sys

from common import MemorySource, Recorder
from postgres_store import PostgresStateStore

from refresh_engine import RefreshEngine, StateStoreABC


async def main() -> None:
    connection_string = os.environ["REFRESH_ENGINE_POSTGRES_DSN"]
    store = PostgresStateStore(connection_string)
    assert isinstance(store, StateStoreABC)
    await store.initialize()

    async with store.transaction() as transaction:
        for resource_id in tuple(await store.load_all()):
            await transaction.delete(resource_id)

    source = MemorySource({"configuration": {"enabled": True}})
    first = RefreshEngine(source, Recorder(), store=store)
    assert (await first.refresh()).refreshed_count == 1
    await first.close()

    second_store = PostgresStateStore(connection_string)
    second = RefreshEngine(source, Recorder(), store=second_store)
    result = await second.refresh()
    assert result.unchanged_count == 1
    assert result.refreshed_count == 0
    await second.close()

    before = await second_store.load_all()
    try:
        async with second_store.transaction() as transaction:
            await transaction.delete("configuration")
            raise RuntimeError("rollback proof")
    except RuntimeError:
        pass
    assert await second_store.load_all() == before
    print("postgres state store: continuity=True, rollback=True")


def _windows_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=_windows_event_loop)
    else:
        asyncio.run(main())
