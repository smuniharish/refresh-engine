"""Durable state with SQLite: unchanged resources stay skipped after a restart."""

import asyncio
import tempfile
from pathlib import Path

from common import DictionarySource, Recorder
from sqlite_store import SQLiteStateStore

from refresh_engine import RefreshEngine


async def run_process(database: Path, contents: dict[str, int]) -> str:
    """Simulate one process lifetime: a fresh engine on the same database."""
    operation = Recorder()
    source = DictionarySource(contents)
    store = SQLiteStateStore(database)
    async with RefreshEngine(source, operation, store=store) as engine:
        result = await engine.refresh()
    return f"{result.status}, operations: {operation.take()}"


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "state.sqlite3"
        print("first start:  ", await run_process(database, {"alpha": 1, "beta": 1}))
        print("restart:      ", await run_process(database, {"alpha": 1, "beta": 1}))
        print("after change: ", await run_process(database, {"alpha": 2}))


if __name__ == "__main__":
    asyncio.run(main())
