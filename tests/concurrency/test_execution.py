from __future__ import annotations

import asyncio

import pytest

from refresh_engine import (
    OverlapPolicy,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
    RetryConfig,
)
from tests.helpers import MutableSource


@pytest.mark.asyncio
async def test_parallelism_is_bounded() -> None:
    active = 0
    maximum = 0
    lock = asyncio.Lock()

    async def operation(
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        nonlocal active, maximum
        async with lock:
            active += 1
            maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1

    engine = RefreshEngine(
        MutableSource({str(index): index for index in range(20)}),
        operation,
        config=RefreshConfig(max_concurrency=3),
    )
    await engine.refresh()
    assert maximum == 3
    await engine.close()


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt() -> None:
    attempts = 0

    async def operation(
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary")

    engine = RefreshEngine(
        MutableSource({"a": 1}),
        operation,
        config=RefreshConfig(retry=RetryConfig(max_attempts=2, initial_delay=0)),
    )
    result = await engine.refresh()
    assert result.refreshed_count == 1
    assert attempts == 2
    await engine.close()


@pytest.mark.asyncio
async def test_cancellation_propagates_without_state_commit() -> None:
    started = asyncio.Event()

    async def operation(
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        started.set()
        await asyncio.Event().wait()

    engine = RefreshEngine(MutableSource({"a": 1}), operation)
    task = asyncio.create_task(engine.refresh())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await engine.store.load_all() == {}
    await engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "expected_status"),
    [
        (OverlapPolicy.SKIP_IF_RUNNING, "skipped"),
        (OverlapPolicy.QUEUE, "success"),
        (OverlapPolicy.COALESCE, "success"),
    ],
)
async def test_overlap_policies(policy: OverlapPolicy, expected_status: str) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation(
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        started.set()
        await release.wait()

    engine = RefreshEngine(
        MutableSource({"a": 1}),
        operation,
        config=RefreshConfig(overlap_policy=policy),
    )
    first = asyncio.create_task(engine.refresh())
    await started.wait()
    second = asyncio.create_task(engine.refresh())
    await asyncio.sleep(0)
    release.set()
    second_result = await second
    await first
    assert second_result.status.value == expected_status
    await engine.close()
