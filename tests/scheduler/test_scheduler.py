from __future__ import annotations

import asyncio

import pytest

from refresh_engine import (
    AsyncScheduler,
    ExternalSchedulerAdapter,
    RefreshRequest,
    RefreshResult,
    SchedulerError,
)


@pytest.mark.asyncio
async def test_async_scheduler_does_not_block_event_loop() -> None:
    calls = 0

    async def callback() -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)

    scheduler = AsyncScheduler(callback, 0.01, run_immediately=True)
    await scheduler.start()
    heartbeats = 0
    for _ in range(5):
        await asyncio.sleep(0.005)
        heartbeats += 1
    await scheduler.stop()
    assert calls >= 1
    assert heartbeats == 5


@pytest.mark.asyncio
async def test_async_scheduler_pause_resume_and_validation() -> None:
    calls = 0

    async def callback() -> None:
        nonlocal calls
        calls += 1

    scheduler = AsyncScheduler(callback, 0.01)
    with pytest.raises(SchedulerError):
        await scheduler.pause()
    await scheduler.start()
    with pytest.raises(SchedulerError):
        await scheduler.start()
    await scheduler.pause()
    paused_at = calls
    await asyncio.sleep(0.03)
    assert calls == paused_at
    await scheduler.resume()
    await asyncio.sleep(0.08)
    assert calls > paused_at
    await scheduler.stop()
    await scheduler.stop()


@pytest.mark.asyncio
async def test_external_scheduler_adapter_delegates() -> None:
    request = RefreshRequest()

    async def submit(value: RefreshRequest) -> RefreshResult:
        assert value is request
        return RefreshResult.skipped("external")

    result = await ExternalSchedulerAdapter(submit).run(request)
    assert result.warnings == ("external",)
