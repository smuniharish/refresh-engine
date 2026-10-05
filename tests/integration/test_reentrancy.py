"""Code that a refresh, a scheduled run, or event delivery waits for."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

import pytest

from refresh_engine import (
    AsyncScheduler,
    DiscoveryResult,
    EngineClosedError,
    EventKind,
    EventPublisher,
    OverlapPolicy,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshEvent,
    RefreshMode,
    RefreshPhase,
    RefreshRequest,
    RefreshResult,
    RefreshStatus,
    Resource,
    ResourceSnapshot,
    SchedulerState,
    TriggerSource,
)
from tests.support import EventRecorder, FakeSource, RecordingOperation

CALLER: ContextVar[str] = ContextVar("caller", default="none")
LIMIT = 5


async def settle() -> None:
    """Let ready tasks run until they block."""
    for _ in range(10):
        await asyncio.sleep(0)


async def test_awaiting_a_refresh_from_an_operation_fails_only_that_operation() -> None:
    errors: list[RuntimeError] = []

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        if resource.resource_id == "a":
            try:
                await engine.refresh(resource_ids="b")
            except RuntimeError as error:
                errors.append(error)
                raise

    engine = RefreshEngine(FakeSource({"a": 1, "b": 1}), operation)
    async with asyncio.timeout(LIMIT):
        result = await engine.refresh()
        await engine.close()
    assert result.status is RefreshStatus.PARTIAL
    (issue,) = result.errors
    assert issue.resource_id == "a"
    assert issue.phase is RefreshPhase.EXECUTION
    assert issue.exception_type == "RuntimeError"
    assert "would wait for itself" in str(errors[0])


async def test_awaiting_a_refresh_from_the_source_fails_the_refresh() -> None:
    class Source:
        async def discover(self) -> DiscoveryResult:
            await engine.submit(RefreshRequest())
            return DiscoveryResult([])

        async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
            return ResourceSnapshot(resource.resource_id)

    engine = RefreshEngine(Source(), RecordingOperation())
    async with asyncio.timeout(LIMIT):
        result = await engine.refresh()
        await engine.close()
    assert result.status is RefreshStatus.FAILED
    (issue,) = result.errors
    assert issue.phase is RefreshPhase.DISCOVERY
    assert issue.exception_type == "RuntimeError"


async def test_an_operation_can_request_a_refresh_without_waiting_for_it() -> None:
    recorder = RecordingOperation()
    follow_ups: list[asyncio.Task[RefreshResult]] = []

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        await recorder(resource, snapshot, action, request)
        if not follow_ups:
            follow_ups.append(asyncio.create_task(engine.refresh(resource_ids="a")))

    engine = RefreshEngine(FakeSource({"a": 1}), operation)
    async with asyncio.timeout(LIMIT):
        first = await engine.refresh()
        follow_up = await follow_ups[0]
        await engine.close()
    assert first.status is RefreshStatus.SUCCESS
    assert follow_up.status is RefreshStatus.SUCCESS
    assert follow_up.request.mode is RefreshMode.TARGETED
    assert recorder.ids() == ["a", "a"]


async def test_closing_from_an_operation_returns_without_waiting() -> None:
    recorder = RecordingOperation()

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        await engine.close()
        assert engine.closed
        await recorder(resource, snapshot, action, request)

    engine = RefreshEngine(
        FakeSource({"a": 1, "b": 1}),
        operation,
        config=RefreshConfig(max_concurrency=1),
    )
    async with asyncio.timeout(LIMIT):
        result = await engine.refresh()
        await engine.close()
    assert result.status is RefreshStatus.SUCCESS
    assert recorder.ids() == ["a", "b"]
    with pytest.raises(EngineClosedError):
        await engine.refresh()


async def test_closing_with_cancel_from_an_operation_cancels_its_refresh() -> None:
    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        await engine.close(cancel=True)
        await asyncio.sleep(0)

    engine = RefreshEngine(FakeSource({"a": 1}), operation)
    async with asyncio.timeout(LIMIT):
        result = await engine.refresh()
    assert result.status is RefreshStatus.CANCELLED
    assert engine.closed


async def test_closing_from_a_task_started_by_an_operation_waits_for_the_refresh() -> (
    None
):
    events = EventPublisher()
    recorder = EventRecorder()
    events.subscribe(recorder)
    shutdowns: list[asyncio.Task[list[EventKind]]] = []

    async def shutdown() -> list[EventKind]:
        await engine.close()
        return recorder.kinds()

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        shutdowns.append(asyncio.create_task(shutdown()))
        await settle()

    engine = RefreshEngine(FakeSource({"a": 1}), operation, events=events)
    async with asyncio.timeout(LIMIT):
        result = await engine.refresh()
        delivered = await shutdowns[0]
    assert result.status is RefreshStatus.SUCCESS
    assert delivered[-1] is EventKind.REFRESH_COMPLETED


async def test_closing_without_waiting_lets_accepted_refreshes_finish() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        started.set()
        await release.wait()

    engine = RefreshEngine(FakeSource({"a": 1}), operation)
    async with asyncio.timeout(LIMIT):
        running = asyncio.create_task(engine.refresh())
        await started.wait()
        await engine.close(wait=False)
        assert engine.closed
        assert not running.done()
        with pytest.raises(EngineClosedError):
            await engine.refresh()
        release.set()
        result = await running
        await engine.close()
    assert result.status is RefreshStatus.SUCCESS


async def test_closing_from_an_event_handler_does_not_wait_for_the_handler() -> None:
    events = EventPublisher()
    recorder = EventRecorder()

    async def close_when_done(event: RefreshEvent) -> None:
        if event.kind is EventKind.REFRESH_COMPLETED:
            await engine.close()

    events.subscribe(close_when_done)
    events.subscribe(recorder)
    engine = RefreshEngine(FakeSource({"a": 1}), RecordingOperation(), events=events)
    async with asyncio.timeout(LIMIT):
        await engine.refresh()
        await engine.close()
    assert engine.closed
    assert recorder.kinds()[-1] is EventKind.REFRESH_COMPLETED
    assert not events.errors


async def test_an_operation_can_stop_the_scheduler_without_waiting() -> None:
    stopped = asyncio.Event()

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        await scheduler.stop(wait=False)
        stopped.set()

    engine = RefreshEngine(FakeSource({"a": 1}), operation)
    scheduler = AsyncScheduler(
        lambda: engine.refresh(trigger=TriggerSource.SCHEDULED),
        60,
        run_immediately=True,
    )
    async with asyncio.timeout(LIMIT):
        await scheduler.start()
        await stopped.wait()
        await scheduler.stop()
        await engine.close()
    assert scheduler.state is SchedulerState.STOPPED


async def test_stopping_without_waiting_from_a_refresh_a_scheduled_run_waits_for() -> (
    None
):
    running = asyncio.Event()
    release = asyncio.Event()
    submitted = asyncio.Event()
    stopped = asyncio.Event()
    scheduled_results: list[RefreshResult] = []

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        if request.correlation_id == "manual":
            running.set()
            await release.wait()
            await scheduler.stop(wait=False)
            stopped.set()

    async def scheduled() -> None:
        submitted.set()
        scheduled_results.append(await engine.refresh(trigger=TriggerSource.SCHEDULED))

    engine = RefreshEngine(FakeSource({"a": 1}), operation)
    scheduler = AsyncScheduler(scheduled, 60, run_immediately=True)
    async with asyncio.timeout(LIMIT):
        manual = asyncio.create_task(
            engine.refresh(mode=RefreshMode.FULL, correlation_id="manual")
        )
        await running.wait()
        await scheduler.start()
        await submitted.wait()
        release.set()
        await stopped.wait()
        await manual
        await scheduler.stop()
        await engine.close()
    assert scheduler.state is SchedulerState.STOPPED
    assert [result.status for result in scheduled_results] == [RefreshStatus.SUCCESS]


async def test_stopping_from_a_task_started_by_the_callback_waits_for_the_run() -> None:
    release = asyncio.Event()
    spawned = asyncio.Event()
    stoppers: list[asyncio.Task[None]] = []

    async def callback() -> None:
        stoppers.append(asyncio.create_task(scheduler.stop()))
        spawned.set()
        await release.wait()

    scheduler = AsyncScheduler(callback, 60, run_immediately=True)
    async with asyncio.timeout(LIMIT):
        await scheduler.start()
        await spawned.wait()
        await settle()
        waiting = not stoppers[0].done()
        state_during_run = scheduler.state
        release.set()
        await stoppers[0]
    assert waiting
    assert state_during_run is SchedulerState.STOPPING
    assert scheduler.state is SchedulerState.STOPPED


async def test_each_queued_refresh_runs_in_the_context_of_its_caller() -> None:
    running = asyncio.Event()
    release = asyncio.Event()
    seen: dict[str | None, tuple[str, str]] = {}

    class Source(FakeSource):
        caller = "unset"

        async def discover(self) -> DiscoveryResult:
            self.caller = CALLER.get()
            return await super().discover()

    source = Source({"a": 1})

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        seen[request.correlation_id] = (source.caller, CALLER.get())
        if request.correlation_id == "first":
            running.set()
            await release.wait()

    engine = RefreshEngine(
        source,
        operation,
        config=RefreshConfig(overlap_policy=OverlapPolicy.QUEUE),
    )

    async def call(name: str) -> RefreshResult:
        CALLER.set(name)
        return await engine.refresh(mode=RefreshMode.FULL, correlation_id=name)

    async with asyncio.timeout(LIMIT):
        first = asyncio.create_task(call("first"))
        await running.wait()
        second = asyncio.create_task(call("second"))
        await settle()
        release.set()
        await asyncio.gather(first, second)
        await engine.close()
    assert seen == {"first": ("first", "first"), "second": ("second", "second")}


async def test_event_handlers_do_not_see_the_context_of_the_caller() -> None:
    events = EventPublisher()
    values: set[str] = set()
    events.subscribe(lambda event: values.add(CALLER.get()))
    engine = RefreshEngine(FakeSource({"a": 1}), RecordingOperation(), events=events)

    async def call() -> RefreshResult:
        CALLER.set("caller")
        return await engine.refresh()

    async with asyncio.timeout(LIMIT):
        await asyncio.create_task(call())
        await engine.close()
    assert values == {"none"}
