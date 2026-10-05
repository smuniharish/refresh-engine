from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from refresh_engine import (
    EngineClosedError,
    EventKind,
    EventPublisher,
    OverlapPolicy,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshMode,
    RefreshRequest,
    RefreshResult,
    RefreshStatus,
    Resource,
    ResourceSnapshot,
    TriggerSource,
)
from tests.support import EventRecorder, FakeSource, RecordingOperation


class Gate:
    """An operation that blocks until released and records concurrency."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []
        self.cancelled: list[str] = []
        self.active = 0
        self.peak = 0

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        self.calls.append(resource.resource_id)
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.append(resource.resource_id)
            raise
        finally:
            self.active -= 1


async def wait_for(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=5)


async def test_operations_respect_max_concurrency() -> None:
    gate = Gate()
    source = FakeSource({f"r{index}": index for index in range(20)})
    engine = RefreshEngine(source, gate, config=RefreshConfig(max_concurrency=3))
    task = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    for _ in range(5):
        await asyncio.sleep(0)
    assert gate.active == 3
    gate.release.set()
    result = await task
    await engine.close()
    assert result.refreshed_count == 20
    assert gate.peak == 3


async def test_snapshot_reads_respect_max_concurrency() -> None:
    active = 0
    peak = 0

    class CountingSource(FakeSource):
        async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.001)
            active -= 1
            return await super().snapshot(resource)

    source = CountingSource({f"r{index}": index for index in range(30)})
    async with RefreshEngine(
        source, RecordingOperation(), config=RefreshConfig(max_concurrency=4)
    ) as engine:
        await engine.refresh()
    assert peak == 4


async def test_dependency_graph_executes_concurrently_in_dependency_order() -> None:
    source = FakeSource(
        {"base": 1, "left": 1, "right": 1, "top": 1, "solo": 1},
        dependencies={"left": {"base"}, "right": {"base"}, "top": {"left", "right"}},
    )
    operation = RecordingOperation(delay=0.001)
    async with RefreshEngine(
        source, operation, config=RefreshConfig(max_concurrency=4)
    ) as engine:
        result = await engine.refresh()
    order = operation.ids()
    assert result.refreshed_count == 5
    assert order.index("base") < order.index("left") < order.index("top")
    assert order.index("base") < order.index("right") < order.index("top")


async def test_skip_if_running_returns_a_skipped_result() -> None:
    gate = Gate()
    events = EventPublisher()
    recorder = EventRecorder()
    events.subscribe(recorder)
    engine = RefreshEngine(
        FakeSource({"a": 1}),
        gate,
        events=events,
        config=RefreshConfig(overlap_policy=OverlapPolicy.SKIP_IF_RUNNING),
    )
    first = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    skipped = await engine.refresh(trigger=TriggerSource.SCHEDULED)
    gate.release.set()
    completed = await first
    await engine.close()
    assert skipped.status is RefreshStatus.SKIPPED
    assert skipped.warnings == (
        "the request was skipped because another refresh is running",
    )
    assert completed.status is RefreshStatus.SUCCESS
    (event,) = recorder.of(EventKind.REFRESH_SKIPPED)
    assert event.refresh_id == skipped.refresh_id
    assert event.attributes["trigger"] == "scheduled"


async def test_queue_runs_every_request_in_order() -> None:
    gate = Gate()
    engine = RefreshEngine(
        FakeSource({"a": 1}),
        gate,
        config=RefreshConfig(overlap_policy=OverlapPolicy.QUEUE),
    )
    first = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    second = asyncio.create_task(engine.refresh(mode=RefreshMode.FULL))
    third = asyncio.create_task(engine.refresh(mode=RefreshMode.FULL))
    await asyncio.sleep(0)
    gate.release.set()
    results = await asyncio.gather(first, second, third)
    await engine.close()
    assert len({result.refresh_id for result in results}) == 3
    assert gate.calls == ["a", "a", "a"]


async def test_coalesce_merges_compatible_queued_requests() -> None:
    gate = Gate()
    engine = RefreshEngine(FakeSource({"a": 1, "b": 2}), gate)
    first = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    incremental = asyncio.create_task(engine.refresh(metadata={"caller": "x"}))
    full = asyncio.create_task(engine.refresh(mode=RefreshMode.FULL))
    targeted = asyncio.create_task(engine.refresh(resource_ids="a"))
    await asyncio.sleep(0)
    gate.release.set()
    (
        first_result,
        incremental_result,
        full_result,
        targeted_result,
    ) = await asyncio.gather(first, incremental, full, targeted)
    await engine.close()
    assert incremental_result is full_result is targeted_result
    assert incremental_result.refresh_id != first_result.refresh_id
    assert incremental_result.request.mode is RefreshMode.FULL
    assert incremental_result.request.metadata == {"caller": "x"}
    assert incremental_result.refreshed_count == 2


async def test_coalesce_queues_requests_that_cannot_be_merged() -> None:
    gate = Gate()
    engine = RefreshEngine(FakeSource({"a": 1, "b": 2}), gate)
    first = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    incremental = asyncio.create_task(engine.refresh())
    targeted = asyncio.create_task(engine.refresh(resource_ids="b"))
    await asyncio.sleep(0)
    gate.release.set()
    _, incremental_result, targeted_result = await asyncio.gather(
        first, incremental, targeted
    )
    await engine.close()
    assert incremental_result.refresh_id != targeted_result.refresh_id
    assert incremental_result.request.mode is RefreshMode.INCREMENTAL
    assert targeted_result.request.resource_ids == {"b"}


async def test_cancel_previous_returns_cancelled_results_to_superseded_callers() -> (
    None
):
    gate = Gate()
    events = EventPublisher()
    recorder = EventRecorder()
    events.subscribe(recorder)
    engine = RefreshEngine(
        FakeSource({"a": 1}),
        gate,
        events=events,
        config=RefreshConfig(overlap_policy=OverlapPolicy.CANCEL_PREVIOUS),
    )
    running = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    queued = asyncio.create_task(engine.refresh(mode=RefreshMode.FULL))
    await asyncio.sleep(0)
    newest = asyncio.create_task(engine.refresh())
    await asyncio.sleep(0)
    gate.release.set()
    running_result, queued_result, newest_result = await asyncio.gather(
        running, queued, newest
    )
    await engine.close()
    reason = "the refresh was superseded by a newer request"
    assert running_result.status is RefreshStatus.CANCELLED
    assert running_result.warnings == (reason,)
    assert queued_result.status is RefreshStatus.CANCELLED
    assert queued_result.warnings == (reason,)
    assert newest_result.status is RefreshStatus.SUCCESS
    assert gate.cancelled == ["a"]
    assert len(recorder.of(EventKind.REFRESH_CANCELLED)) == 2


async def test_cancelling_the_caller_cancels_the_refresh_without_committing() -> None:
    gate = Gate()
    events = EventPublisher()
    recorder = EventRecorder()
    events.subscribe(recorder)
    engine = RefreshEngine(FakeSource({"a": 1}), gate, events=events)
    task = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await engine.close()
    assert await engine.store.load_all() == {}
    assert gate.cancelled == ["a"]
    (event,) = recorder.of(EventKind.REFRESH_CANCELLED)
    assert event.attributes["status"] == "cancelled"


async def test_a_coalesced_caller_leaving_does_not_cancel_the_others() -> None:
    gate = Gate()
    engine = RefreshEngine(FakeSource({"a": 1}), gate)
    first = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    leaving = asyncio.create_task(engine.refresh())
    staying = asyncio.create_task(engine.refresh())
    await asyncio.sleep(0)
    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving
    gate.release.set()
    assert (await first).status is RefreshStatus.SUCCESS
    assert (await staying).status is RefreshStatus.SUCCESS
    await engine.close()


async def test_a_queued_request_without_callers_never_runs() -> None:
    gate = Gate()
    engine = RefreshEngine(
        FakeSource({"a": 1}),
        gate,
        config=RefreshConfig(overlap_policy=OverlapPolicy.QUEUE),
    )
    first = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    abandoned = asyncio.create_task(engine.refresh(mode=RefreshMode.FULL))
    await asyncio.sleep(0)
    abandoned.cancel()
    with pytest.raises(asyncio.CancelledError):
        await abandoned
    gate.release.set()
    await first
    await engine.close()
    assert gate.calls == ["a"]


async def test_one_of_two_active_callers_leaving_keeps_the_refresh_running() -> None:
    permits = asyncio.Semaphore(0)
    started: list[asyncio.Event] = [asyncio.Event(), asyncio.Event()]
    cancelled = False

    async def turnstile(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        nonlocal cancelled
        started[0 if not started[0].is_set() else 1].set()
        try:
            await permits.acquire()
        except asyncio.CancelledError:
            cancelled = True
            raise

    engine = RefreshEngine(FakeSource({"a": 1}), turnstile)
    blocker = asyncio.create_task(engine.refresh())
    await wait_for(started[0])
    leaving = asyncio.create_task(engine.refresh(mode=RefreshMode.FULL))
    staying = asyncio.create_task(engine.refresh(mode=RefreshMode.FULL))
    await asyncio.sleep(0)
    permits.release()
    await blocker
    await wait_for(started[1])
    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving
    permits.release()
    assert (await staying).status is RefreshStatus.SUCCESS
    await engine.close()
    assert not cancelled


async def test_close_waits_for_running_and_queued_refreshes() -> None:
    gate = Gate()
    engine = RefreshEngine(
        FakeSource({"a": 1}),
        gate,
        config=RefreshConfig(overlap_policy=OverlapPolicy.QUEUE),
    )
    first = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    second = asyncio.create_task(engine.refresh(mode=RefreshMode.FULL))
    await asyncio.sleep(0)
    closing = asyncio.create_task(engine.close())
    await asyncio.sleep(0)
    assert engine.closed
    assert not closing.done()
    with pytest.raises(EngineClosedError):
        await engine.refresh()
    gate.release.set()
    await closing
    assert (await first).status is RefreshStatus.SUCCESS
    assert (await second).status is RefreshStatus.SUCCESS


async def test_close_with_cancel_returns_cancelled_results() -> None:
    gate = Gate()
    engine = RefreshEngine(
        FakeSource({"a": 1}),
        gate,
        config=RefreshConfig(overlap_policy=OverlapPolicy.QUEUE),
    )
    first = asyncio.create_task(engine.refresh())
    await wait_for(gate.started)
    second = asyncio.create_task(engine.refresh(mode=RefreshMode.FULL))
    await asyncio.sleep(0)
    await engine.close(cancel=True)
    reason = "the refresh was cancelled because the engine is closing"
    for result in (await first, await second):
        assert result.status is RefreshStatus.CANCELLED
        assert result.warnings == (reason,)


async def test_close_is_idempotent_and_closing_an_idle_engine_is_immediate() -> None:
    engine = RefreshEngine(FakeSource({}), RecordingOperation())
    assert not engine.closed
    await engine.close()
    await engine.close()
    assert engine.closed
    with pytest.raises(EngineClosedError, match="the refresh engine is closed"):
        await engine.submit(RefreshRequest())


async def test_leaving_the_context_with_an_error_cancels_active_work() -> None:
    gate = Gate()
    engine = RefreshEngine(FakeSource({"a": 1}), gate)
    tasks: list[asyncio.Task[RefreshResult]] = []

    async def use_engine() -> None:
        async with engine:
            tasks.append(asyncio.create_task(engine.refresh()))
            await wait_for(gate.started)
            raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        await use_engine()
    assert (await tasks[0]).status is RefreshStatus.CANCELLED
    assert gate.cancelled == ["a"]


async def test_engine_can_be_reused_across_event_loops() -> None:
    engine = RefreshEngine(FakeSource({"a": 1}), RecordingOperation())

    def run_in_new_loop() -> RefreshStatus:
        return asyncio.run(engine.refresh()).status

    first = await asyncio.to_thread(run_in_new_loop)
    second = await asyncio.to_thread(run_in_new_loop)
    assert (first, second) == (RefreshStatus.SUCCESS, RefreshStatus.SUCCESS)


async def test_submit_rejects_non_requests() -> None:
    async with RefreshEngine(FakeSource({}), RecordingOperation()) as engine:
        with pytest.raises(TypeError, match="RefreshRequest"):
            await engine.submit(cast(Any, {"mode": "full"}))
