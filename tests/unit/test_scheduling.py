"""Tests for `AsyncScheduler` and `ExternalSchedulerAdapter`."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import math
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import pytest

from refresh_engine import (
    AsyncScheduler,
    ConfigurationError,
    EventHandler,
    EventKind,
    EventPublisher,
    EventPublisherABC,
    ExternalSchedulerAdapter,
    RefreshEvent,
    RefreshMode,
    RefreshRequest,
    RefreshResult,
    RefreshStatus,
    SchedulerError,
    SchedulerState,
    TriggerSource,
)
from tests.support import EventRecorder

TIMEOUT = 5
INTERVAL = 0.01
LONG_INTERVAL = 3600.0


def exactly(message: str) -> str:
    return f"^{re.escape(message)}$"


async def bounded[T](awaitable: Awaitable[T]) -> T:
    return await asyncio.wait_for(awaitable, timeout=TIMEOUT)


async def let_other_tasks_run() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


class Calls:
    """A callback that counts its runs and lets tests wait for them."""

    def __init__(self) -> None:
        self.count = 0
        self.times: list[float] = []
        self._changed = asyncio.Event()

    async def __call__(self) -> None:
        self.count += 1
        self.times.append(asyncio.get_running_loop().time())
        self._changed.set()

    async def wait_until(self, count: int) -> None:
        async def reached() -> None:
            while self.count < count:
                self._changed.clear()
                await self._changed.wait()

        await bounded(reached())


class BlockingRun:
    """A callback whose runs block until `release` is set."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.done = asyncio.Event()
        self.finished = 0
        self.cancelled = 0

    async def __call__(self) -> None:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.finished += 1
        self.done.set()


class SchedulerFactory(Protocol):
    def __call__(
        self,
        callback: Callable[[], Awaitable[object]],
        interval: float = INTERVAL,
        *,
        events: EventPublisherABC | None = None,
        run_immediately: bool = False,
    ) -> AsyncScheduler: ...


@pytest.fixture
async def make_scheduler() -> AsyncIterator[SchedulerFactory]:
    """Create schedulers that are always stopped when the test ends."""
    created: list[AsyncScheduler] = []

    def make(
        callback: Callable[[], Awaitable[object]],
        interval: float = INTERVAL,
        *,
        events: EventPublisherABC | None = None,
        run_immediately: bool = False,
    ) -> AsyncScheduler:
        scheduler = AsyncScheduler(
            callback, interval, events=events, run_immediately=run_immediately
        )
        created.append(scheduler)
        return scheduler

    yield make
    for scheduler in created:
        await scheduler.stop(cancel=True)


def recording_publisher() -> tuple[EventPublisher, EventRecorder]:
    publisher = EventPublisher()
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    return publisher, recorder


class ListPublisher(EventPublisherABC):
    """Collects published events synchronously; `has_subscribers` is configurable."""

    def __init__(self, *, subscribed: bool) -> None:
        self.subscribed = subscribed
        self.events: list[RefreshEvent] = []

    @property
    def has_subscribers(self) -> bool:
        return self.subscribed

    def subscribe(self, handler: EventHandler) -> None:
        return None

    def unsubscribe(self, handler: EventHandler) -> None:
        return None

    def publish(self, event: RefreshEvent) -> None:
        self.events.append(event)

    async def drain(self) -> None:
        return None


# Construction


def test_scheduler_requires_a_callable_callback() -> None:
    with pytest.raises(TypeError, match=exactly("callback must be callable")):
        AsyncScheduler(cast(Any, None), interval=1)


@pytest.mark.parametrize(
    ("interval", "message"),
    [
        (0, "interval must be greater than 0"),
        (-1, "interval must be greater than 0"),
        (math.inf, "interval must be a finite number"),
        (math.nan, "interval must be a finite number"),
        (True, "interval must be a finite number"),
        ("1", "interval must be a finite number"),
    ],
    ids=["zero", "negative", "inf", "nan", "bool", "str"],
)
def test_scheduler_rejects_invalid_intervals(interval: object, message: str) -> None:
    with pytest.raises(ConfigurationError, match=exactly(message)):
        AsyncScheduler(Calls(), interval=cast(Any, interval))


def test_new_scheduler_is_stopped() -> None:
    scheduler = AsyncScheduler(Calls(), interval=2.5)

    assert scheduler.state is SchedulerState.STOPPED
    assert scheduler.interval == 2.5


# Lifecycle


async def test_scheduler_moves_through_its_lifecycle_states(
    make_scheduler: SchedulerFactory,
) -> None:
    scheduler = make_scheduler(Calls(), LONG_INTERVAL)
    observed = [scheduler.state]

    await scheduler.start()
    observed.append(scheduler.state)
    await scheduler.pause()
    observed.append(scheduler.state)
    await scheduler.resume()
    observed.append(scheduler.state)
    await bounded(scheduler.stop())
    observed.append(scheduler.state)

    assert observed == [
        SchedulerState.STOPPED,
        SchedulerState.RUNNING,
        SchedulerState.PAUSED,
        SchedulerState.RUNNING,
        SchedulerState.STOPPED,
    ]


async def test_start_rejects_a_started_scheduler(
    make_scheduler: SchedulerFactory,
) -> None:
    scheduler = make_scheduler(Calls(), LONG_INTERVAL)
    await scheduler.start()

    with pytest.raises(
        SchedulerError, match=exactly("the scheduler is already started")
    ):
        await scheduler.start()
    await scheduler.pause()
    with pytest.raises(
        SchedulerError, match=exactly("the scheduler is already started")
    ):
        await scheduler.start()


async def test_pause_requires_a_running_scheduler(
    make_scheduler: SchedulerFactory,
) -> None:
    scheduler = make_scheduler(Calls(), LONG_INTERVAL)
    message = exactly("only a running scheduler can be paused")

    with pytest.raises(SchedulerError, match=message):
        await scheduler.pause()
    await scheduler.start()
    await scheduler.pause()
    with pytest.raises(SchedulerError, match=message):
        await scheduler.pause()


async def test_resume_requires_a_paused_scheduler(
    make_scheduler: SchedulerFactory,
) -> None:
    scheduler = make_scheduler(Calls(), LONG_INTERVAL)
    message = exactly("only a paused scheduler can be resumed")

    with pytest.raises(SchedulerError, match=message):
        await scheduler.resume()
    await scheduler.start()
    with pytest.raises(SchedulerError, match=message):
        await scheduler.resume()


# Scheduled runs


async def test_run_immediately_runs_the_callback_when_started(
    make_scheduler: SchedulerFactory,
) -> None:
    calls = Calls()
    scheduler = make_scheduler(calls, LONG_INTERVAL, run_immediately=True)

    await scheduler.start()

    await calls.wait_until(1)


async def test_first_run_waits_for_one_interval_by_default(
    make_scheduler: SchedulerFactory,
) -> None:
    calls = Calls()
    scheduler = make_scheduler(calls, LONG_INTERVAL)

    await scheduler.start()
    await let_other_tasks_run()

    assert calls.count == 0


async def test_first_run_starts_one_interval_after_start(
    make_scheduler: SchedulerFactory,
) -> None:
    interval = 0.05
    calls = Calls()
    scheduler = make_scheduler(calls, interval)
    started_at = asyncio.get_running_loop().time()

    await scheduler.start()
    await calls.wait_until(1)

    # The scheduler never runs before the slot is due on the loop clock; the
    # tolerance only absorbs floating-point rounding.
    assert calls.times[0] - started_at >= interval - 1e-9


async def test_runs_follow_the_interval_grid_measured_from_start(
    make_scheduler: SchedulerFactory,
) -> None:
    calls = Calls()
    scheduler = make_scheduler(calls)
    started_at = asyncio.get_running_loop().time()

    await scheduler.start()
    await calls.wait_until(3)

    # Runs may start late but never before their slot on the loop clock.
    for slot, ran_at in enumerate(calls.times[:3], start=1):
        assert ran_at - started_at >= slot * INTERVAL - 1e-9


async def test_runs_never_overlap(make_scheduler: SchedulerFactory) -> None:
    active = 0
    most_active = 0
    calls = Calls()

    async def slow_run() -> None:
        nonlocal active, most_active
        active += 1
        most_active = max(most_active, active)
        await asyncio.sleep(INTERVAL * 3)
        active -= 1
        await calls()

    scheduler = make_scheduler(slow_run, run_immediately=True)
    await scheduler.start()
    await calls.wait_until(3)
    await bounded(scheduler.stop())

    assert most_active == 1


async def test_pause_skips_runs_until_resumed(make_scheduler: SchedulerFactory) -> None:
    calls = Calls()
    scheduler = make_scheduler(calls)
    await scheduler.start()
    await calls.wait_until(1)

    await scheduler.pause()
    paused_count = calls.count
    await asyncio.sleep(INTERVAL * 10)
    assert calls.count == paused_count

    await scheduler.resume()
    await calls.wait_until(paused_count + 1)
    assert scheduler.state is SchedulerState.RUNNING


async def test_pause_lets_an_active_run_finish(
    make_scheduler: SchedulerFactory,
) -> None:
    run = BlockingRun()
    scheduler = make_scheduler(run, LONG_INTERVAL, run_immediately=True)
    await scheduler.start()
    await bounded(run.started.wait())

    await scheduler.pause()
    run.release.set()
    await bounded(run.done.wait())

    assert run.finished == 1
    assert run.cancelled == 0
    assert scheduler.state is SchedulerState.PAUSED


async def test_paused_scheduler_can_be_stopped(
    make_scheduler: SchedulerFactory,
) -> None:
    calls = Calls()
    scheduler = make_scheduler(calls, LONG_INTERVAL)
    await scheduler.start()
    await scheduler.pause()
    await let_other_tasks_run()

    await bounded(scheduler.stop())

    assert scheduler.state is SchedulerState.STOPPED
    assert calls.count == 0


# Stopping


async def test_stop_waits_for_the_active_run(make_scheduler: SchedulerFactory) -> None:
    run = BlockingRun()
    scheduler = make_scheduler(run, LONG_INTERVAL, run_immediately=True)
    await scheduler.start()
    await bounded(run.started.wait())

    stopping = asyncio.create_task(scheduler.stop())
    await let_other_tasks_run()

    assert not stopping.done()
    state_while_waiting = scheduler.state
    with pytest.raises(SchedulerError, match="already started"):
        await scheduler.start()

    run.release.set()
    await bounded(stopping)
    assert state_while_waiting is SchedulerState.STOPPING
    assert run.finished == 1
    assert run.cancelled == 0
    assert scheduler.state is SchedulerState.STOPPED


async def test_stop_with_cancel_cancels_the_active_run(
    make_scheduler: SchedulerFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="refresh_engine")
    publisher, recorder = recording_publisher()
    run = BlockingRun()
    scheduler = make_scheduler(
        run, LONG_INTERVAL, events=publisher, run_immediately=True
    )
    await scheduler.start()
    await bounded(run.started.wait())

    await bounded(scheduler.stop(cancel=True))
    await bounded(publisher.drain())

    assert run.cancelled == 1
    assert run.finished == 0
    assert scheduler.state is SchedulerState.STOPPED
    assert recorder.kinds() == [
        EventKind.SCHEDULER_STARTED,
        EventKind.SCHEDULER_STOPPED,
    ]
    assert [
        record for record in caplog.records if record.name == "refresh_engine"
    ] == []


async def test_stopping_a_scheduler_that_never_started_does_nothing() -> None:
    publisher, recorder = recording_publisher()
    scheduler = AsyncScheduler(Calls(), INTERVAL, events=publisher)

    await scheduler.stop()
    await bounded(publisher.drain())

    assert scheduler.state is SchedulerState.STOPPED
    assert recorder.events == []


async def test_stop_is_idempotent(make_scheduler: SchedulerFactory) -> None:
    publisher, recorder = recording_publisher()
    scheduler = make_scheduler(Calls(), LONG_INTERVAL, events=publisher)
    await scheduler.start()

    await bounded(scheduler.stop())
    await bounded(scheduler.stop())
    await bounded(publisher.drain())

    assert recorder.kinds() == [
        EventKind.SCHEDULER_STARTED,
        EventKind.SCHEDULER_STOPPED,
    ]


async def test_concurrent_stops_publish_one_stopped_event(
    make_scheduler: SchedulerFactory,
) -> None:
    publisher, recorder = recording_publisher()
    run = BlockingRun()
    scheduler = make_scheduler(
        run, LONG_INTERVAL, events=publisher, run_immediately=True
    )
    await scheduler.start()
    await bounded(run.started.wait())

    stops = asyncio.gather(scheduler.stop(), scheduler.stop(), scheduler.stop())
    await let_other_tasks_run()
    assert not stops.done()
    run.release.set()
    await bounded(stops)
    await bounded(publisher.drain())

    assert scheduler.state is SchedulerState.STOPPED
    assert recorder.kinds().count(EventKind.SCHEDULER_STOPPED) == 1
    assert run.finished == 1


async def test_scheduler_can_be_restarted_after_stopping(
    make_scheduler: SchedulerFactory,
) -> None:
    publisher, recorder = recording_publisher()
    calls = Calls()
    scheduler = make_scheduler(calls, LONG_INTERVAL, events=publisher)
    await scheduler.start()
    await bounded(scheduler.stop())

    await scheduler.start()
    await let_other_tasks_run()

    assert scheduler.state is SchedulerState.RUNNING
    assert calls.count == 0
    await bounded(scheduler.stop())
    await bounded(publisher.drain())
    assert recorder.kinds() == [
        EventKind.SCHEDULER_STARTED,
        EventKind.SCHEDULER_STOPPED,
        EventKind.SCHEDULER_STARTED,
        EventKind.SCHEDULER_STOPPED,
    ]


async def test_restarted_scheduler_runs_immediately_again_when_configured(
    make_scheduler: SchedulerFactory,
) -> None:
    calls = Calls()
    scheduler = make_scheduler(calls, LONG_INTERVAL, run_immediately=True)
    await scheduler.start()
    await calls.wait_until(1)
    await bounded(scheduler.stop())

    await scheduler.start()

    await calls.wait_until(2)


async def test_restarted_scheduler_keeps_running_on_its_interval(
    make_scheduler: SchedulerFactory,
) -> None:
    calls = Calls()
    scheduler = make_scheduler(calls)
    await scheduler.start()
    await calls.wait_until(1)
    await bounded(scheduler.stop())
    stopped_count = calls.count

    await scheduler.start()

    await calls.wait_until(stopped_count + 2)


# Failures and events


async def test_callback_failures_are_logged_published_and_do_not_stop_the_schedule(
    make_scheduler: SchedulerFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="refresh_engine")
    publisher, recorder = recording_publisher()
    calls = Calls()
    failure = ValueError("callback failed")

    async def fail_first_run() -> None:
        await calls()
        if calls.count == 1:
            raise failure

    scheduler = make_scheduler(fail_first_run, events=publisher, run_immediately=True)
    await scheduler.start()
    await calls.wait_until(2)
    await bounded(scheduler.stop())
    await bounded(publisher.drain())

    (event,) = recorder.of(EventKind.SCHEDULER_RUN_FAILED)
    assert event.attributes == {"error_type": "ValueError"}
    assert event.error is failure
    assert event.refresh_id is None
    (record,) = [
        record
        for record in caplog.records
        if record.name == "refresh_engine" and record.levelno == logging.ERROR
    ]
    assert record.getMessage() == "scheduled refresh callback failed"
    assert record.exc_info is not None
    assert record.exc_info[1] is failure
    assert scheduler.state is SchedulerState.STOPPED


async def test_scheduler_publishes_started_and_stopped_events(
    make_scheduler: SchedulerFactory,
) -> None:
    publisher, recorder = recording_publisher()
    scheduler = make_scheduler(Calls(), LONG_INTERVAL, events=publisher)

    await scheduler.start()
    await bounded(scheduler.stop())
    await bounded(publisher.drain())

    assert recorder.kinds() == [
        EventKind.SCHEDULER_STARTED,
        EventKind.SCHEDULER_STOPPED,
    ]
    assert recorder.attributes(EventKind.SCHEDULER_STARTED) == {
        "interval": LONG_INTERVAL
    }
    assert recorder.attributes(EventKind.SCHEDULER_STOPPED) == {}
    assert all(event.refresh_id is None for event in recorder.events)


async def test_scheduler_publishes_to_any_event_publisher(
    make_scheduler: SchedulerFactory,
) -> None:
    publisher = ListPublisher(subscribed=True)
    scheduler = make_scheduler(Calls(), LONG_INTERVAL, events=publisher)

    await scheduler.start()
    await bounded(scheduler.stop())

    assert [event.kind for event in publisher.events] == [
        EventKind.SCHEDULER_STARTED,
        EventKind.SCHEDULER_STOPPED,
    ]


async def test_scheduler_skips_events_when_nobody_subscribes(
    make_scheduler: SchedulerFactory,
) -> None:
    publisher = ListPublisher(subscribed=False)
    scheduler = make_scheduler(Calls(), LONG_INTERVAL, events=publisher)

    await scheduler.start()
    await bounded(scheduler.stop())

    assert publisher.events == []


# Context manager


async def test_context_manager_starts_and_stops_the_scheduler(
    make_scheduler: SchedulerFactory,
) -> None:
    scheduler = make_scheduler(Calls(), LONG_INTERVAL)

    async with scheduler as entered:
        assert entered is scheduler
        assert entered.state is SchedulerState.RUNNING

    assert scheduler.state is SchedulerState.STOPPED


async def test_context_manager_waits_for_the_active_run_on_normal_exit(
    make_scheduler: SchedulerFactory,
) -> None:
    run = BlockingRun()
    scheduler = make_scheduler(run, LONG_INTERVAL, run_immediately=True)

    async with scheduler:
        await bounded(run.started.wait())
        asyncio.get_running_loop().call_soon(run.release.set)

    assert run.finished == 1
    assert run.cancelled == 0
    assert scheduler.state is SchedulerState.STOPPED


async def test_context_manager_cancels_the_active_run_when_the_block_raises(
    make_scheduler: SchedulerFactory,
) -> None:
    run = BlockingRun()
    scheduler = make_scheduler(run, LONG_INTERVAL, run_immediately=True)

    async def fail_during_run() -> None:
        async with scheduler:
            await bounded(run.started.wait())
            raise RuntimeError("block failed")

    with pytest.raises(RuntimeError, match="block failed"):
        await fail_during_run()

    assert run.cancelled == 1
    assert run.finished == 0
    assert scheduler.state is SchedulerState.STOPPED


# Slot arithmetic


@pytest.mark.parametrize(
    ("now", "inclusive", "expected"),
    [
        pytest.param(100.0, False, 110.0, id="after-run-at-origin"),
        pytest.param(105.0, False, 110.0, id="after-run-mid-slot"),
        pytest.param(110.0, False, 120.0, id="after-run-on-boundary"),
        pytest.param(135.0, False, 140.0, id="after-run-skips-missed-slots"),
        pytest.param(100.0, True, 110.0, id="resume-at-origin"),
        pytest.param(105.0, True, 110.0, id="resume-mid-slot"),
        pytest.param(110.0, True, 110.0, id="resume-on-boundary"),
        pytest.param(135.0, True, 140.0, id="resume-skips-missed-slots"),
    ],
)
def test_next_slot_stays_on_the_interval_grid(
    now: float, inclusive: bool, expected: float
) -> None:
    scheduler = AsyncScheduler(Calls(), interval=10)

    # Exercised directly: hitting exact slot boundaries through the loop clock
    # cannot be done deterministically.
    assert scheduler._next_slot(100.0, now, inclusive=inclusive) == expected


async def test_stop_from_inside_the_callback_stops_after_the_callback_returns(
    make_scheduler: SchedulerFactory,
) -> None:
    publisher, recorder = recording_publisher()
    finished = asyncio.Event()
    scheduler: AsyncScheduler | None = None

    async def stop_itself() -> None:
        assert scheduler is not None
        await scheduler.stop()
        assert scheduler.state is SchedulerState.STOPPING
        finished.set()

    scheduler = make_scheduler(stop_itself, events=publisher, run_immediately=True)
    await scheduler.start()
    await bounded(finished.wait())
    for _ in range(100):
        if scheduler.state is SchedulerState.STOPPED:
            break
        await asyncio.sleep(0)
    await publisher.drain()
    assert scheduler.state is SchedulerState.STOPPED
    assert recorder.kinds().count(EventKind.SCHEDULER_STOPPED) == 1


async def test_a_failing_event_publisher_does_not_break_the_scheduler(
    make_scheduler: SchedulerFactory, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenPublisher(ListPublisher):
        def publish(self, event: RefreshEvent) -> None:
            raise RuntimeError("bus down")

    calls = Calls()
    scheduler = make_scheduler(
        calls, events=BrokenPublisher(subscribed=True), run_immediately=True
    )
    await scheduler.start()
    await calls.wait_until(1)
    await scheduler.stop()
    assert scheduler.state is SchedulerState.STOPPED
    assert "failed to publish scheduler_started event" in caplog.text
    assert "failed to publish scheduler_stopped event" in caplog.text


# ExternalSchedulerAdapter


def make_result(request: RefreshRequest) -> RefreshResult:
    now = datetime.now(UTC)
    return RefreshResult(
        refresh_id="refresh-1",
        request=request,
        status=RefreshStatus.SUCCESS,
        started_at=now,
        completed_at=now,
        duration=0.0,
    )


class FakeSubmit:
    """Records submitted requests and the thread that ran them."""

    def __init__(self) -> None:
        self.requests: list[RefreshRequest] = []
        self.threads: list[int] = []

    async def __call__(self, request: RefreshRequest) -> RefreshResult:
        self.requests.append(request)
        self.threads.append(threading.get_ident())
        await asyncio.sleep(0)
        return make_result(request)


async def test_adapter_requires_a_callable_submit() -> None:
    with pytest.raises(TypeError, match=exactly("submit must be callable")):
        ExternalSchedulerAdapter(cast(Any, "engine.submit"))


def test_adapter_requires_a_running_loop_when_no_loop_is_given() -> None:
    with pytest.raises(RuntimeError, match="no running event loop"):
        ExternalSchedulerAdapter(FakeSubmit())


async def test_run_submits_an_external_incremental_request_by_default() -> None:
    submit = FakeSubmit()
    adapter = ExternalSchedulerAdapter(submit)

    result = await adapter.run()

    (request,) = submit.requests
    assert request.trigger is TriggerSource.EXTERNAL
    assert request.mode is RefreshMode.INCREMENTAL
    assert result.request is request


async def test_run_passes_an_explicit_request_through_unchanged() -> None:
    submit = FakeSubmit()
    adapter = ExternalSchedulerAdapter(submit)
    request = RefreshRequest(mode=RefreshMode.FULL, trigger=TriggerSource.EVENT)

    result = await adapter.run(request)

    assert submit.requests == [request]
    assert submit.requests[0] is request
    assert result.request is request


async def test_submit_threadsafe_runs_the_request_on_the_adapter_loop() -> None:
    submit = FakeSubmit()
    adapter = ExternalSchedulerAdapter(submit)
    request = RefreshRequest(mode=RefreshMode.FULL)

    future = await asyncio.to_thread(adapter.submit_threadsafe, request)
    result = await bounded(asyncio.wrap_future(future))

    assert isinstance(future, concurrent.futures.Future)
    assert result.request is request
    assert submit.threads == [threading.get_ident()]


async def test_submit_threadsafe_uses_the_default_request() -> None:
    submit = FakeSubmit()
    adapter = ExternalSchedulerAdapter(submit)

    future = await asyncio.to_thread(adapter.submit_threadsafe)
    result = await bounded(asyncio.wrap_future(future))

    assert result.request.trigger is TriggerSource.EXTERNAL
    assert result.request.mode is RefreshMode.INCREMENTAL


async def test_run_threadsafe_returns_the_result_to_another_thread() -> None:
    submit = FakeSubmit()
    adapter = ExternalSchedulerAdapter(submit)

    result = await bounded(asyncio.to_thread(adapter.run_threadsafe, timeout=TIMEOUT))

    (request,) = submit.requests
    assert result.request is request
    assert request.trigger is TriggerSource.EXTERNAL
    assert submit.threads == [threading.get_ident()]


async def test_run_threadsafe_passes_an_explicit_request_through() -> None:
    submit = FakeSubmit()
    adapter = ExternalSchedulerAdapter(submit)
    request = RefreshRequest(trigger=TriggerSource.SCHEDULED)

    result = await bounded(asyncio.to_thread(adapter.run_threadsafe, request))

    assert result.request is request


async def test_run_threadsafe_refuses_to_block_the_adapter_loop() -> None:
    submit = FakeSubmit()
    adapter = ExternalSchedulerAdapter(submit)

    with pytest.raises(
        RuntimeError,
        match=exactly("run_threadsafe() would block the event loop; use run() instead"),
    ):
        adapter.run_threadsafe()
    await let_other_tasks_run()

    assert submit.requests == []


async def test_run_threadsafe_timeout_cancels_the_submitted_coroutine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    cancelled = asyncio.Event()
    never = asyncio.Event()

    async def submit(request: RefreshRequest) -> RefreshResult:
        started.set()
        try:
            await never.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return make_result(request)

    adapter = ExternalSchedulerAdapter(submit)
    submit_threadsafe = adapter.submit_threadsafe
    futures: list[concurrent.futures.Future[RefreshResult]] = []

    def submit_once_running(
        request: RefreshRequest | None = None,
    ) -> concurrent.futures.Future[RefreshResult]:
        # Hand the future back only once the coroutine runs, so the timeout always
        # expires while the submission is in flight.
        future = submit_threadsafe(request)
        futures.append(future)
        assert started.wait(timeout=TIMEOUT)
        return future

    monkeypatch.setattr(adapter, "submit_threadsafe", submit_once_running)

    with pytest.raises(TimeoutError):
        await asyncio.to_thread(adapter.run_threadsafe, timeout=0.01)

    await bounded(cancelled.wait())
    assert futures[0].cancelled()


def test_adapter_bound_to_an_explicit_loop_runs_requests_on_that_loop() -> None:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        submit = FakeSubmit()
        adapter = ExternalSchedulerAdapter(submit, loop=loop)

        result = adapter.run_threadsafe(timeout=TIMEOUT)

        assert result.request.trigger is TriggerSource.EXTERNAL
        assert submit.threads == [thread.ident]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=TIMEOUT)
        loop.close()
