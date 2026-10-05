"""Durable state with PostgreSQL.

Set `REFRESH_ENGINE_POSTGRES_DSN` to a PostgreSQL connection string, for example
`postgresql://postgres:postgres@localhost:5432/postgres`, before running this
example. It creates and clears the table `refresh_engine_state`.
"""

import asyncio
import os
import sys

from common import DictionarySource, Recorder
from postgres_store import PostgresStateStore

from refresh_engine import RefreshEngine


async def run_process(store: PostgresStateStore, contents: dict[str, int]) -> str:
    """Simulate one process lifetime: a fresh engine on the same database."""
    operation = Recorder()
    async with RefreshEngine(
        DictionarySource(contents), operation, store=store
    ) as engine:
        result = await engine.refresh()
    return f"{result.status}, operations: {operation.take()}"


async def main() -> None:
    store = PostgresStateStore(os.environ["REFRESH_ENGINE_POSTGRES_DSN"])
    await store.create_schema()
    await store.clear()
    print("first start:  ", await run_process(store, {"alpha": 1, "beta": 1}))
    print("restart:      ", await run_process(store, {"alpha": 1, "beta": 1}))
    print("after change: ", await run_process(store, {"alpha": 2}))


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main())
