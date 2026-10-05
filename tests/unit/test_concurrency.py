from __future__ import annotations

import asyncio

import pytest

from refresh_engine._internal.concurrency import run_bounded, run_workers


async def test_run_bounded_ignores_empty_input() -> None:
    calls: list[int] = []

    async def work(item: int) -> None:
        calls.append(item)

    await run_bounded([], work, 4, lambda: False)
    assert calls == []


async def test_run_bounded_limits_concurrency_and_visits_every_item() -> None:
    active = 0
    peak = 0
    seen: list[int] = []

    async def work(item: int) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        seen.append(item)
        active -= 1

    await run_bounded(range(10), work, 3, lambda: False)
    assert sorted(seen) == list(range(10))
    assert peak == 3


async def test_run_bounded_stops_taking_items_once_asked() -> None:
    seen: list[int] = []

    async def work(item: int) -> None:
        seen.append(item)

    await run_bounded(range(10), work, 1, lambda: len(seen) >= 4)
    assert seen == [0, 1, 2, 3]


@pytest.mark.parametrize("count", [1, 3])
async def test_run_workers_reraises_the_first_worker_exception(count: int) -> None:
    async def worker() -> None:
        await asyncio.sleep(0)
        raise LookupError("bug")

    with pytest.raises(LookupError, match="bug"):
        await run_workers(worker, count)
