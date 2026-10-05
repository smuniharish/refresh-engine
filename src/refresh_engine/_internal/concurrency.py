"""Bounded concurrency without a task per item."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import Any

from refresh_engine._internal.reentrancy import claim


async def run_workers(
    worker: Callable[[], Coroutine[Any, Any, None]], count: int
) -> None:
    """Run `count` copies of `worker` concurrently and wait for all of them.

    Worker tasks work for the same owner as the calling task. An exception that
    escapes a worker cancels the others and is re-raised unwrapped.
    """
    if count == 1:
        await worker()
        return
    try:
        async with asyncio.TaskGroup() as group:
            for _ in range(count):
                group.create_task(_claimed(worker))
    except ExceptionGroup as failures:
        raise failures.exceptions[0] from failures


async def _claimed(worker: Callable[[], Coroutine[Any, Any, None]]) -> None:
    claim()
    await worker()


async def run_bounded[T](
    items: Sequence[T],
    work: Callable[[T], Awaitable[object]],
    limit: int,
    should_stop: Callable[[], bool],
) -> None:
    """Call `work` for every item with at most `limit` calls in flight.

    Workers share one iterator, so no queue or per-item task is needed. A worker
    stops taking new items once `should_stop()` returns `True`; calls in flight
    finish normally. `work` is expected to handle its own exceptions.
    """
    if not items:
        return
    iterator = iter(items)

    async def worker() -> None:
        for item in iterator:
            if should_stop():
                return
            await work(item)

    await run_workers(worker, min(limit, len(items)))
