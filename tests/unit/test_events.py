"""Tests for lifecycle events and the default `AsyncEventPublisher`."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from refresh_engine import (
    AsyncEventPublisher,
    ConfigurationError,
    EventHandler,
    EventHandlerCallable,
    EventHandlerLike,
    EventKind,
    EventPublisher,
    PlanAction,
    RefreshEngine,
    RefreshEvent,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)
from tests.support import EventRecorder, FakeSource

TIMEOUT = 5


def event(kind: EventKind = EventKind.REFRESH_STARTED) -> RefreshEvent:
    return RefreshEvent(kind)


async def drain(publisher: AsyncEventPublisher) -> None:
    await asyncio.wait_for(publisher.drain(), timeout=TIMEOUT)


def publisher_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "refresh_engine" and record.levelno == logging.WARNING
    ]


class MinimalPublisher(EventPublisher):
    """Implements only the abstract methods, keeping the default `has_subscribers`."""

    def subscribe(self, handler: EventHandlerLike) -> None:
        return None

    def unsubscribe(self, handler: EventHandlerLike) -> None:
        return None

    def publish(self, event: RefreshEvent) -> None:
        return None

    async def drain(self) -> None:
        return None


# RefreshEvent


def test_event_kind_values_are_lowercase_member_names() -> None:
    assert all(kind.value == kind.name.lower() for kind in EventKind)


def test_refresh_event_defaults() -> None:
    before = datetime.now(UTC)
    created = RefreshEvent(EventKind.SCHEDULER_STOPPED)
    after = datetime.now(UTC)

    assert created.kind is EventKind.SCHEDULER_STOPPED
    assert created.refresh_id is None
    assert created.resource_id is None
    assert created.correlation_id is None
    assert created.attributes == {}
    assert created.error is None
    assert created.timestamp.tzinfo is UTC
    assert before <= created.timestamp <= after


def test_refresh_event_attributes_are_an_independent_read_only_copy() -> None:
    attributes: dict[str, Any] = {"mode": "full"}

    created = RefreshEvent(EventKind.REFRESH_STARTED, attributes=attributes)
    attributes["mode"] = "incremental"

    assert created.attributes == {"mode": "full"}
    with pytest.raises(TypeError):
        cast(Any, created.attributes)["mode"] = "targeted"


@pytest.mark.parametrize("field", ["kind", "refresh_id", "attributes", "error"])
def test_refresh_event_is_frozen(field: str) -> None:
    created = RefreshEvent(EventKind.REFRESH_STARTED, refresh_id="r-1")

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(created, field, None)


def test_refresh_event_equality_and_hash_ignore_the_error() -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    failed = RefreshEvent(
        EventKind.REFRESH_FAILED,
        refresh_id="r-1",
        timestamp=timestamp,
        attributes={"errors": ["boom"]},
        error=ValueError("boom"),
    )
    same = RefreshEvent(
        EventKind.REFRESH_FAILED,
        refresh_id="r-1",
        timestamp=timestamp,
        attributes={"errors": ["boom"]},
    )

    assert failed == same
    assert hash(failed) == hash(same)


# EventPublisher


def test_event_handler_abc_requires_an_async_call_method() -> None:
    with pytest.raises(TypeError, match="abstract"):
        cast(Any, EventHandler)()


def test_event_publisher_abc_requires_its_abstract_methods() -> None:
    with pytest.raises(TypeError, match="abstract"):
        cast(Any, EventPublisher)()


def test_event_publisher_abc_reports_subscribers_by_default() -> None:
    assert MinimalPublisher().has_subscribers is True


def test_event_publisher_is_an_event_publisher_abc() -> None:
    assert isinstance(AsyncEventPublisher(), EventPublisher)


# Subscriptions


def test_publisher_tracks_whether_it_has_subscribers() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    observed = [publisher.has_subscribers]

    publisher.subscribe(recorder)
    observed.append(publisher.has_subscribers)
    publisher.unsubscribe(recorder)
    observed.append(publisher.has_subscribers)

    assert observed == [False, True, False]


@pytest.mark.parametrize("handler", [None, "handler", 1, lambda _: None])
def test_subscribe_rejects_handlers_that_are_not_abc_or_async_functions(
    handler: object,
) -> None:
    publisher = AsyncEventPublisher()

    with pytest.raises(
        TypeError,
        match=(r"^event handlers must subclass EventHandler or be async functions$"),
    ):
        publisher.subscribe(cast(Any, handler))

    assert publisher.has_subscribers is False


async def test_subscribing_the_same_handler_twice_delivers_each_event_once() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    published = event()

    publisher.subscribe(recorder)
    publisher.subscribe(recorder)
    publisher.publish(published)
    await drain(publisher)

    assert recorder.events == [published]


async def test_a_bound_method_accessed_twice_is_the_same_handler() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()

    publisher.subscribe(recorder.__call__)
    publisher.subscribe(recorder.__call__)
    publisher.publish(event())
    await drain(publisher)
    publisher.unsubscribe(recorder.__call__)

    assert len(recorder.events) == 1
    assert publisher.has_subscribers is False


async def test_distinct_handlers_that_compare_equal_each_receive_every_event() -> None:
    publisher = AsyncEventPublisher()
    first, second = EventRecorder(), EventRecorder()
    published = event()

    publisher.subscribe(first)
    publisher.subscribe(second)
    publisher.publish(published)
    await drain(publisher)

    assert first.events == [published]
    assert second.events == [published]


async def test_unsubscribing_an_unknown_handler_has_no_effect() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    published = event()
    publisher.subscribe(recorder)

    async def unknown(_: RefreshEvent) -> None:
        return None

    publisher.unsubscribe(unknown)
    publisher.publish(published)
    await drain(publisher)

    assert publisher.has_subscribers is True
    assert recorder.events == [published]


async def test_unsubscribed_handlers_stop_receiving_events() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    first = event()
    publisher.publish(first)
    await drain(publisher)

    publisher.unsubscribe(recorder)
    publisher.publish(event(EventKind.REFRESH_COMPLETED))
    await drain(publisher)

    assert recorder.events == [first]


def numbered_handler(delivered: list[int], number: int) -> EventHandlerCallable:
    async def handler(_: RefreshEvent) -> None:
        delivered.append(number)

    return handler


async def test_subscriptions_from_many_threads_are_all_kept() -> None:
    publisher = AsyncEventPublisher()
    delivered: list[int] = []
    batches = [
        [numbered_handler(delivered, batch * 100 + index) for index in range(100)]
        for batch in range(8)
    ]

    def apply(
        action: Callable[[EventHandlerLike], None], batch: list[EventHandlerLike]
    ) -> None:
        for handler in batch:
            action(handler)

    await asyncio.gather(
        *(asyncio.to_thread(apply, publisher.subscribe, batch) for batch in batches)
    )
    publisher.publish(event())
    await drain(publisher)
    assert sorted(delivered) == list(range(800))

    await asyncio.gather(
        *(asyncio.to_thread(apply, publisher.unsubscribe, batch) for batch in batches)
    )
    assert publisher.has_subscribers is False


# Publication without subscribers


def test_publish_without_subscribers_is_a_no_op_without_a_running_loop() -> None:
    publisher = AsyncEventPublisher()

    publisher.publish(event())

    assert publisher.errors == ()


def test_publish_with_subscribers_requires_a_running_loop() -> None:
    publisher = AsyncEventPublisher()
    publisher.subscribe(EventRecorder())

    with pytest.raises(RuntimeError, match="no running event loop"):
        publisher.publish(event())


async def test_events_published_without_subscribers_are_dropped() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    later = event(EventKind.REFRESH_COMPLETED)
    publisher.publish(event(EventKind.REFRESH_STARTED))

    publisher.subscribe(recorder)
    publisher.publish(later)
    await drain(publisher)

    assert recorder.events == [later]


# Delivery


async def test_handlers_receive_events_in_publication_order() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    published = [event(kind) for kind in EventKind]

    for item in published:
        publisher.publish(item)
    await drain(publisher)

    assert recorder.events == published


async def test_each_event_reaches_handlers_in_subscription_order() -> None:
    publisher = AsyncEventPublisher()
    log: list[tuple[str, EventKind]] = []

    async def first(item: RefreshEvent) -> None:
        log.append(("first", item.kind))

    async def second(item: RefreshEvent) -> None:
        log.append(("second", item.kind))

    publisher.subscribe(first)
    publisher.subscribe(second)

    publisher.publish(event(EventKind.REFRESH_STARTED))
    publisher.publish(event(EventKind.REFRESH_COMPLETED))
    await drain(publisher)

    assert log == [
        ("first", EventKind.REFRESH_STARTED),
        ("second", EventKind.REFRESH_STARTED),
        ("first", EventKind.REFRESH_COMPLETED),
        ("second", EventKind.REFRESH_COMPLETED),
    ]


async def test_async_handlers_are_awaited_before_the_next_handler_runs() -> None:
    publisher = AsyncEventPublisher()
    log: list[tuple[str, EventKind]] = []

    async def slow(item: RefreshEvent) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        log.append(("slow", item.kind))

    publisher.subscribe(slow)

    async def fast(item: RefreshEvent) -> None:
        log.append(("fast", item.kind))

    publisher.subscribe(fast)
    publisher.publish(event(EventKind.REFRESH_STARTED))
    publisher.publish(event(EventKind.REFRESH_COMPLETED))
    await drain(publisher)

    assert log == [
        ("slow", EventKind.REFRESH_STARTED),
        ("fast", EventKind.REFRESH_STARTED),
        ("slow", EventKind.REFRESH_COMPLETED),
        ("fast", EventKind.REFRESH_COMPLETED),
    ]


async def test_async_function_handlers_are_awaited() -> None:
    publisher = AsyncEventPublisher()
    finished: list[EventKind] = []

    async def finish(item: RefreshEvent) -> None:
        await asyncio.sleep(0)
        finished.append(item.kind)

    publisher.subscribe(finish)
    publisher.publish(event(EventKind.SCHEDULER_STARTED))
    await drain(publisher)

    assert finished == [EventKind.SCHEDULER_STARTED]


async def test_async_handler_return_values_are_ignored() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    published = event()

    async def ignored_result(_: RefreshEvent) -> int:
        return 42

    publisher.subscribe(ignored_result)
    publisher.subscribe(recorder)

    publisher.publish(published)
    await drain(publisher)

    assert recorder.events == [published]
    assert publisher.errors == ()


async def test_handler_exceptions_are_isolated_recorded_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="refresh_engine")
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    published = event(EventKind.REFRESH_STARTED)
    raised: list[Exception] = []

    async def failing(item: RefreshEvent) -> None:
        error = ValueError(f"cannot handle {item.kind.value}")
        raised.append(error)
        raise error

    publisher.subscribe(failing)
    publisher.subscribe(recorder)
    publisher.publish(published)
    await drain(publisher)

    assert recorder.events == [published]
    assert publisher.errors == tuple(raised)
    (record,) = publisher_warnings(caplog)
    message = record.getMessage()
    assert message.startswith(
        f"refresh event handler {failing!r} failed for refresh_started event\n"
        "Traceback (most recent call last):\n"
    )
    assert message.endswith("ValueError: cannot handle refresh_started")
    assert record.exc_info is None


async def test_async_handler_exceptions_are_isolated() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    first = event(EventKind.REFRESH_STARTED)
    second = event(EventKind.REFRESH_COMPLETED)
    failure = RuntimeError("async failure")

    async def failing(item: RefreshEvent) -> None:
        await asyncio.sleep(0)
        raise failure

    publisher.subscribe(failing)
    publisher.subscribe(recorder)
    publisher.publish(first)
    publisher.publish(second)
    await drain(publisher)

    assert recorder.events == [first, second]
    assert publisher.errors == (failure, failure)


async def test_errors_keep_only_the_most_recent_exceptions() -> None:
    publisher = AsyncEventPublisher(max_errors=2)
    raised: list[Exception] = []

    async def failing(item: RefreshEvent) -> None:
        error = ValueError(str(len(raised)))
        raised.append(error)
        raise error

    publisher.subscribe(failing)
    for _ in range(3):
        publisher.publish(event())
    await drain(publisher)

    assert len(raised) == 3
    assert publisher.errors == (raised[1], raised[2])


@pytest.mark.parametrize("max_errors", [0, -1, True, 1.5, None])
def test_max_errors_must_be_a_positive_integer(max_errors: object) -> None:
    with pytest.raises(
        ConfigurationError,
        match=f"^{re.escape('max_errors must be an integer of at least 1')}$",
    ):
        AsyncEventPublisher(max_errors=cast(Any, max_errors))


async def test_drain_waits_until_queued_events_are_delivered() -> None:
    publisher = AsyncEventPublisher()
    published = event()
    gate = asyncio.Event()
    delivered: list[RefreshEvent] = []

    async def blocked(item: RefreshEvent) -> None:
        await gate.wait()
        delivered.append(item)

    publisher.subscribe(blocked)
    publisher.publish(published)
    draining = asyncio.create_task(publisher.drain())
    for _ in range(5):
        await asyncio.sleep(0)

    assert not draining.done()
    assert delivered == []

    gate.set()
    await asyncio.wait_for(draining, timeout=TIMEOUT)
    assert delivered == [published]


async def test_drain_returns_without_suspending_when_idle() -> None:
    publisher = AsyncEventPublisher()
    probe_ran = asyncio.Event()

    async def probe() -> None:
        probe_ran.set()

    probe_task = asyncio.create_task(probe())
    await publisher.drain()

    assert not probe_ran.is_set()
    await asyncio.wait_for(probe_task, timeout=TIMEOUT)
    assert probe_ran.is_set()


async def test_drain_returns_without_suspending_after_delivery_finished() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    publisher.publish(event())
    await drain(publisher)
    probe_ran = asyncio.Event()

    async def probe() -> None:
        probe_ran.set()

    probe_task = asyncio.create_task(probe())
    await publisher.drain()

    assert not probe_ran.is_set()
    assert len(recorder.events) == 1
    await asyncio.wait_for(probe_task, timeout=TIMEOUT)


async def test_drain_from_a_handler_returns_without_waiting_for_itself() -> None:
    publisher = AsyncEventPublisher()
    delivered: list[EventKind] = []

    async def draining(item: RefreshEvent) -> None:
        delivered.append(item.kind)
        if item.kind is EventKind.REFRESH_STARTED:
            await publisher.drain()

    publisher.subscribe(draining)
    publisher.publish(event(EventKind.REFRESH_STARTED))
    publisher.publish(event(EventKind.REFRESH_COMPLETED))
    await asyncio.wait_for(publisher.drain(), timeout=TIMEOUT)

    assert delivered == [EventKind.REFRESH_STARTED, EventKind.REFRESH_COMPLETED]
    assert publisher.errors == ()


async def test_events_published_by_handlers_during_dispatch_are_delivered() -> None:
    publisher = AsyncEventPublisher()
    received: list[EventKind] = []

    async def republish(item: RefreshEvent) -> None:
        received.append(item.kind)
        if item.kind is EventKind.REFRESH_STARTED:
            publisher.publish(event(EventKind.REFRESH_COMPLETED))

    publisher.subscribe(republish)
    publisher.publish(event(EventKind.REFRESH_STARTED))
    await drain(publisher)

    assert received == [EventKind.REFRESH_STARTED, EventKind.REFRESH_COMPLETED]


async def test_events_published_while_a_handler_awaits_are_delivered_in_order() -> None:
    publisher = AsyncEventPublisher()
    first = event(EventKind.REFRESH_STARTED)
    second = event(EventKind.REFRESH_COMPLETED)
    gate = asyncio.Event()
    started = asyncio.Event()
    received: list[RefreshEvent] = []

    async def handler(item: RefreshEvent) -> None:
        started.set()
        await gate.wait()
        received.append(item)

    publisher.subscribe(handler)
    publisher.publish(first)
    await asyncio.wait_for(started.wait(), timeout=TIMEOUT)
    publisher.publish(second)
    gate.set()
    await drain(publisher)

    assert received == [first, second]


async def test_publishing_after_delivery_finished_starts_a_new_delivery() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    first = event(EventKind.REFRESH_STARTED)
    second = event(EventKind.REFRESH_COMPLETED)
    publisher.subscribe(recorder)

    publisher.publish(first)
    await drain(publisher)
    publisher.publish(second)
    await drain(publisher)

    assert recorder.events == [first, second]


async def test_dispatcher_yields_to_the_event_loop_during_long_deliveries() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    total = 1000
    observed: list[int] = []

    async def probe() -> None:
        observed.append(len(recorder.events))

    for _ in range(total):
        publisher.publish(event())
    probe_task = asyncio.create_task(probe())
    await drain(publisher)
    await asyncio.wait_for(probe_task, timeout=TIMEOUT)

    assert len(recorder.events) == total
    # The probe task was created after the dispatcher task, so it runs after the
    # dispatcher's first step; it can only see a partial delivery if the
    # dispatcher yielded to the loop part-way through.
    assert 0 < observed[0] < total


def resource_event(index: int) -> RefreshEvent:
    return RefreshEvent(EventKind.RESOURCE_REFRESH_COMPLETED, resource_id=str(index))


def test_capacity_is_validated_and_exposed() -> None:
    assert AsyncEventPublisher().capacity == 100_000
    assert AsyncEventPublisher(capacity=5).capacity == 5
    for value in (0, -1, 1.5, True):
        with pytest.raises(
            ConfigurationError,
            match=re.escape("capacity must be an integer of at least 1"),
        ):
            AsyncEventPublisher(capacity=cast(Any, value))


async def test_queue_under_normal_load_delivers_everything() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    recorder = EventRecorder()
    publisher.subscribe(recorder)

    for index in range(5):
        publisher.publish(resource_event(index))
    await drain(publisher)

    assert [item.resource_id for item in recorder.events] == list("01234")
    assert publisher.dropped == 0
    assert publisher.queued == 0
    assert publisher.peak_queued == 5


async def test_saturated_queue_drops_newest_events_and_logs_once_per_episode(
    caplog: pytest.LogCaptureFixture,
) -> None:
    publisher = AsyncEventPublisher(capacity=3)
    recorder = EventRecorder()
    publisher.subscribe(recorder)

    for index in range(6):
        publisher.publish(resource_event(index))
    assert publisher.queued == 3
    assert publisher.dropped == 3
    await drain(publisher)
    for index in range(6, 10):
        publisher.publish(resource_event(index))
    await drain(publisher)

    assert [item.resource_id for item in recorder.events] == [
        "0",
        "1",
        "2",
        "6",
        "7",
        "8",
    ]
    assert publisher.dropped == 4
    assert publisher.peak_queued == 3
    warnings = publisher_warnings(caplog)
    assert len(warnings) == 2
    assert warnings[0].getMessage() == (
        "refresh event queue is full (capacity 3); dropping events until handlers "
        "catch up"
    )


async def test_boundary_events_use_the_reserve_when_the_queue_is_full() -> None:
    publisher = AsyncEventPublisher(capacity=2)
    recorder = EventRecorder()
    publisher.subscribe(recorder)

    publisher.publish(event(EventKind.REFRESH_STARTED))
    publisher.publish(resource_event(1))
    publisher.publish(resource_event(2))
    publisher.publish(event(EventKind.REFRESH_COMPLETED))
    publisher.publish(event(EventKind.SCHEDULER_STOPPED))
    await drain(publisher)

    assert [item.kind for item in recorder.events] == [
        EventKind.REFRESH_STARTED,
        EventKind.RESOURCE_REFRESH_COMPLETED,
        EventKind.REFRESH_COMPLETED,
        EventKind.SCHEDULER_STOPPED,
    ]
    assert publisher.dropped == 1


async def test_sustained_production_keeps_memory_bounded() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    publisher.subscribe(EventRecorder())

    for index in range(5_000):
        publisher.publish(resource_event(index))
        publisher.publish(event(EventKind.REFRESH_FAILED))

    assert publisher.queued == 10 + AsyncEventPublisher.BOUNDARY_RESERVE
    assert publisher.peak_queued == publisher.queued
    assert publisher.dropped == 10_000 - publisher.queued
    await drain(publisher)
    assert publisher.queued == 0


async def test_slow_subscriber_never_blocks_publishing_and_subscribers_agree() -> None:
    publisher = AsyncEventPublisher(capacity=4)
    release = asyncio.Event()
    slow_seen: list[str | None] = []
    fast = EventRecorder()

    async def slow(item: RefreshEvent) -> None:
        await release.wait()
        slow_seen.append(item.resource_id)

    publisher.subscribe(slow)
    publisher.subscribe(fast)
    publisher.publish(resource_event(0))
    await asyncio.sleep(0)
    for index in range(1, 10):
        publisher.publish(resource_event(index))
    assert publisher.queued == 4
    release.set()
    await drain(publisher)

    assert slow_seen == ["0", "1", "2", "3", "4"]
    assert [item.resource_id for item in fast.events] == slow_seen
    assert publisher.dropped == 5


async def test_handler_failure_under_saturation_does_not_stop_delivery() -> None:
    publisher = AsyncEventPublisher(capacity=2)
    recorder = EventRecorder()

    async def broken(item: RefreshEvent) -> None:
        raise RuntimeError(item.resource_id)

    publisher.subscribe(broken)
    publisher.subscribe(recorder)
    for index in range(4):
        publisher.publish(resource_event(index))
    await drain(publisher)

    assert [item.resource_id for item in recorder.events] == ["0", "1"]
    assert [str(error) for error in publisher.errors] == ["0", "1"]


async def cancel_dispatcher(publisher: AsyncEventPublisher) -> None:
    dispatcher = publisher._dispatcher
    assert dispatcher is not None
    dispatcher.cancel()
    await asyncio.wait((dispatcher,))


def live_dispatchers() -> list[asyncio.Task[Any]]:
    return [
        task
        for task in asyncio.all_tasks()
        if task.get_name() == "refresh-engine-event-dispatcher" and not task.done()
    ]


class Gate(EventHandler):
    """Handler that blocks on the first `blocks` deliveries of resource "0"."""

    def __init__(self, blocks: int = 1) -> None:
        self.blocks = blocks
        self.entered = asyncio.Event()
        self.calls: list[str | None] = []

    async def __call__(self, item: RefreshEvent) -> None:
        self.calls.append(item.resource_id)
        if item.resource_id == "0" and self.blocks:
            self.blocks -= 1
            self.entered.set()
            await asyncio.Event().wait()


async def test_drain_redelivers_the_event_interrupted_by_dispatcher_cancellation() -> (
    None
):
    publisher = AsyncEventPublisher(capacity=10)
    gate = Gate()
    publisher.subscribe(gate)
    for index in range(3):
        publisher.publish(resource_event(index))
    await gate.entered.wait()

    await cancel_dispatcher(publisher)
    assert publisher.queued == 2
    await drain(publisher)

    assert gate.calls == ["0", "0", "1", "2"]
    assert publisher.queued == 0
    assert publisher.errors == ()
    assert live_dispatchers() == []


async def test_cancelling_before_delivery_starts_keeps_every_event_queued() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    for index in range(3):
        publisher.publish(resource_event(index))

    await cancel_dispatcher(publisher)
    assert publisher.queued == 3
    await drain(publisher)

    assert [item.resource_id for item in recorder.events] == ["0", "1", "2"]
    assert publisher.queued == 0


async def test_handlers_after_the_interrupted_one_receive_the_event_once() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    before, after = EventRecorder(), EventRecorder()
    gate = Gate()
    publisher.subscribe(before)
    publisher.subscribe(gate)
    publisher.subscribe(after)
    publisher.publish(resource_event(0))
    await gate.entered.wait()

    await cancel_dispatcher(publisher)
    assert publisher.queued == 0
    await drain(publisher)

    assert [item.resource_id for item in before.events] == ["0"]
    assert gate.calls == ["0", "0"]
    assert [item.resource_id for item in after.events] == ["0"]


async def test_publishing_after_cancellation_redelivers_before_new_events() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    gate = Gate()
    publisher.subscribe(gate)
    publisher.publish(resource_event(0))
    publisher.publish(resource_event(1))
    await gate.entered.wait()
    await cancel_dispatcher(publisher)

    publisher.publish(resource_event(2))
    publisher.publish(resource_event(3))
    await drain(publisher)

    assert gate.calls == ["0", "0", "1", "2", "3"]


async def test_repeated_cancellation_and_restart_cycles_lose_no_events() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    gate = Gate(blocks=3)
    publisher.subscribe(gate)
    publisher.publish(resource_event(0))
    publisher.publish(resource_event(1))
    for _ in range(3):
        await gate.entered.wait()
        gate.entered.clear()
        await cancel_dispatcher(publisher)
        assert publisher.queued == 1
        assert live_dispatchers() == []
        if gate.blocks:
            publisher._start()

    await drain(publisher)

    assert gate.calls == ["0", "0", "0", "0", "1"]
    assert publisher.queued == 0
    assert publisher.dropped == 0
    assert live_dispatchers() == []


async def test_an_unsubscribed_interrupted_handler_is_not_called_again() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    gate = Gate()
    after = EventRecorder()
    publisher.subscribe(gate)
    publisher.subscribe(after)
    publisher.publish(resource_event(0))
    await gate.entered.wait()
    await cancel_dispatcher(publisher)

    publisher.unsubscribe(gate)
    await drain(publisher)

    assert gate.calls == ["0"]
    assert [item.resource_id for item in after.events] == ["0"]


async def test_a_handler_raising_cancelled_error_on_its_own_is_a_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    publisher = AsyncEventPublisher(capacity=10)
    recorder = EventRecorder()

    async def cancelled(item: RefreshEvent) -> None:
        raise asyncio.CancelledError

    publisher.subscribe(cancelled)
    publisher.subscribe(recorder)
    publisher.publish(resource_event(0))
    publisher.publish(resource_event(1))
    await drain(publisher)

    assert [item.resource_id for item in recorder.events] == ["0", "1"]
    assert publisher.errors == ()
    assert len(publisher_warnings(caplog)) == 2
    assert publisher.queued == 0


async def test_drain_leaves_events_queued_when_no_handler_remains() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    publisher.publish(resource_event(0))
    dispatcher = publisher._dispatcher
    assert dispatcher is not None
    dispatcher.cancel()
    await asyncio.wait((dispatcher,))
    publisher.unsubscribe(recorder)

    await drain(publisher)

    assert publisher.queued == 1
    assert recorder.events == []


def lifecycle(
    kind: EventKind, refresh_id: str, resource_id: str | None = None
) -> RefreshEvent:
    return RefreshEvent(kind, refresh_id=refresh_id, resource_id=resource_id)


def flood(publisher: AsyncEventPublisher) -> None:
    for _ in range(publisher.capacity + AsyncEventPublisher.BOUNDARY_RESERVE + 10):
        publisher.publish(event(EventKind.SCHEDULER_RUN_FAILED))


async def test_closing_events_are_kept_when_the_reserve_is_exhausted() -> None:
    publisher = AsyncEventPublisher(capacity=4)
    recorder = EventRecorder()
    publisher.subscribe(recorder)

    publisher.publish(lifecycle(EventKind.REFRESH_STARTED, "r"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "a"))
    flood(publisher)
    full = publisher.queued
    assert full == 4 + AsyncEventPublisher.BOUNDARY_RESERVE
    publisher.publish(lifecycle(EventKind.REFRESH_STARTED, "late"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "late", "b"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_FAILED, "r", "a"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_FAILED, "r", "a"))
    publisher.publish(lifecycle(EventKind.REFRESH_FAILED, "r"))
    publisher.publish(lifecycle(EventKind.REFRESH_FAILED, "r"))
    publisher.publish(lifecycle(EventKind.REFRESH_FAILED, "late"))
    assert publisher.queued == full + 2
    assert publisher.peak_queued == full + 2
    await drain(publisher)

    delivered = [
        (item.kind, item.refresh_id, item.resource_id)
        for item in recorder.events
        if item.kind is not EventKind.SCHEDULER_RUN_FAILED
    ]
    assert delivered == [
        (EventKind.REFRESH_STARTED, "r", None),
        (EventKind.RESOURCE_REFRESH_STARTED, "r", "a"),
        (EventKind.RESOURCE_REFRESH_FAILED, "r", "a"),
        (EventKind.REFRESH_FAILED, "r", None),
    ]
    assert publisher._lifecycles == {}
    assert publisher._tracked == 0


async def deliver(publisher: AsyncEventPublisher, item: RefreshEvent) -> None:
    publisher.publish(item)
    await drain(publisher)


async def test_full_lifecycle_tracking_rejects_new_starts_instead_of_evicting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="refresh_engine")
    publisher = AsyncEventPublisher(capacity=3)
    recorder = EventRecorder()
    publisher.subscribe(recorder)

    for refresh_id in ("r1", "r2"):
        await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, refresh_id))
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r2", "a"))
    assert publisher.open_lifecycles == 3
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r2", "b"))
    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r3"))
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r3", "c"))
    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r1"))
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r2", "a"))
    assert publisher._lifecycles == {"r1": set(), "r2": {"a"}}
    assert publisher.open_lifecycles == 3
    assert publisher.rejected_lifecycles == 2
    assert publisher.dropped == 2
    flood(publisher)
    for closer in (  # The queue is full, so only tracked lifecycles are closed.
        lifecycle(EventKind.RESOURCE_REFRESH_COMPLETED, "r2", "b"),
        lifecycle(EventKind.RESOURCE_REFRESH_COMPLETED, "r2", "a"),
        lifecycle(EventKind.REFRESH_COMPLETED, "r1"),
        lifecycle(EventKind.REFRESH_COMPLETED, "r2"),
        lifecycle(EventKind.REFRESH_COMPLETED, "r3"),
    ):
        publisher.publish(closer)
    await drain(publisher)

    delivered = [
        (item.kind, item.refresh_id, item.resource_id)
        for item in recorder.events
        if item.kind is not EventKind.SCHEDULER_RUN_FAILED
    ]
    assert delivered == [
        (EventKind.REFRESH_STARTED, "r1", None),
        (EventKind.REFRESH_STARTED, "r2", None),
        (EventKind.RESOURCE_REFRESH_STARTED, "r2", "a"),
        (EventKind.RESOURCE_REFRESH_STARTED, "r3", "c"),
        (EventKind.REFRESH_STARTED, "r1", None),
        (EventKind.RESOURCE_REFRESH_STARTED, "r2", "a"),
        (EventKind.RESOURCE_REFRESH_COMPLETED, "r2", "a"),
        (EventKind.REFRESH_COMPLETED, "r1", None),
        (EventKind.REFRESH_COMPLETED, "r2", None),
    ]
    assert publisher.open_lifecycles == 0
    assert [record.getMessage() for record in publisher_warnings(caplog)] == [
        "3 refresh lifecycles are open (capacity 3); not delivering new lifecycle "
        "starts until some close",
        "refresh event queue is full (capacity 3); dropping events until handlers "
        "catch up",
    ]

    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r4"))
    assert publisher._lifecycles == {"r4": set()}
    assert publisher.rejected_lifecycles == 2


@pytest.mark.parametrize(
    "closer",
    [
        EventKind.REFRESH_COMPLETED,
        EventKind.REFRESH_FAILED,
        EventKind.REFRESH_CANCELLED,
        EventKind.REFRESH_SKIPPED,
    ],
)
async def test_every_terminal_event_releases_lifecycle_capacity(
    closer: EventKind,
) -> None:
    publisher = AsyncEventPublisher(capacity=2)
    recorder = EventRecorder()
    publisher.subscribe(recorder)

    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r1"))
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r1", "a"))
    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r2"))
    await deliver(publisher, lifecycle(closer, "r1"))
    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r2"))
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r2", "b"))
    await drain(publisher)

    assert publisher._lifecycles == {"r2": {"b"}}
    assert publisher.rejected_lifecycles == 1
    assert [item.refresh_id for item in recorder.events] == [
        "r1",
        "r1",
        "r1",
        "r2",
        "r2",
    ]


@pytest.mark.parametrize(
    "closer",
    [
        EventKind.RESOURCE_REFRESH_COMPLETED,
        EventKind.RESOURCE_REFRESH_FAILED,
        EventKind.RESOURCE_REFRESH_SKIPPED,
    ],
)
async def test_every_resource_closer_releases_lifecycle_capacity(
    closer: EventKind,
) -> None:
    publisher = AsyncEventPublisher(capacity=2)
    publisher.subscribe(EventRecorder())

    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r"))
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "a"))
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "b"))
    await deliver(publisher, lifecycle(closer, "r", "a"))
    await deliver(publisher, lifecycle(closer, "r", "a"))
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "b"))
    await drain(publisher)

    assert publisher._lifecycles == {"r": {"b"}}
    assert publisher.open_lifecycles == 2
    assert publisher.rejected_lifecycles == 1


async def test_concurrent_refreshes_never_exceed_the_lifecycle_capacity() -> None:
    publisher = AsyncEventPublisher(capacity=4)
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    peak = 0

    async def refresh(number: int) -> None:
        nonlocal peak
        refresh_id = f"r{number}"
        publisher.publish(lifecycle(EventKind.REFRESH_STARTED, refresh_id))
        for resource_id in ("a", "b"):
            publisher.publish(
                lifecycle(EventKind.RESOURCE_REFRESH_STARTED, refresh_id, resource_id)
            )
            peak = max(peak, publisher.open_lifecycles)
            await asyncio.sleep(0)
            publisher.publish(
                lifecycle(EventKind.RESOURCE_REFRESH_COMPLETED, refresh_id, resource_id)
            )
        publisher.publish(lifecycle(EventKind.REFRESH_COMPLETED, refresh_id))

    await asyncio.gather(*(refresh(number) for number in range(20)))
    await drain(publisher)

    assert peak == 4
    assert publisher.open_lifecycles == 0
    assert publisher.rejected_lifecycles > 0
    opened: dict[tuple[str | None, str | None], int] = {}
    for item in recorder.events:
        key = (item.refresh_id, item.resource_id)
        if item.kind in {EventKind.REFRESH_STARTED, EventKind.RESOURCE_REFRESH_STARTED}:
            opened[key] = opened.get(key, 0) + 1
        elif key in opened:
            opened[key] -= 1
    assert set(opened.values()) == {0}
    assert (
        sum(item.kind is EventKind.REFRESH_STARTED for item in recorder.events)
        + publisher.rejected_lifecycles
        >= 20
    )


def kinds(recorder: EventRecorder) -> list[tuple[EventKind, str | None, str | None]]:
    return [(item.kind, item.refresh_id, item.resource_id) for item in recorder.events]


async def test_the_last_unsubscribed_handler_still_receives_open_lifecycle_ends() -> (
    None
):
    publisher = AsyncEventPublisher(capacity=2)
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    publisher.publish(lifecycle(EventKind.REFRESH_STARTED, "r"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "a"))
    await drain(publisher)
    publisher.unsubscribe(recorder)
    subscribed = [publisher.has_subscribers]

    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "b"))
    publisher.publish(lifecycle(EventKind.DISCOVERY_COMPLETED, "r"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_COMPLETED, "r", "a"))
    assert publisher._lifecycles == {"r": set()}
    publisher.publish(lifecycle(EventKind.REFRESH_COMPLETED, "r"))
    publisher.publish(lifecycle(EventKind.REFRESH_COMPLETED, "r"))
    await drain(publisher)

    assert kinds(recorder) == [
        (EventKind.REFRESH_STARTED, "r", None),
        (EventKind.RESOURCE_REFRESH_STARTED, "r", "a"),
        (EventKind.RESOURCE_REFRESH_COMPLETED, "r", "a"),
        (EventKind.REFRESH_COMPLETED, "r", None),
    ]
    assert publisher.open_lifecycles == 0
    assert publisher._recipients == {}
    subscribed.append(publisher.has_subscribers)
    assert subscribed == [True, False]
    assert publisher.dropped == 0


async def test_unsubscribing_stops_new_lifecycles_but_closes_open_ones() -> None:
    publisher = AsyncEventPublisher()
    leaving, staying = EventRecorder(), EventRecorder()
    publisher.subscribe(leaving)
    publisher.subscribe(staying)
    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r1"))
    publisher.unsubscribe(leaving)

    for item in (
        lifecycle(EventKind.DISCOVERY_STARTED, "r1"),
        lifecycle(EventKind.REFRESH_STARTED, "r2"),
        lifecycle(EventKind.REFRESH_FAILED, "r1"),
        lifecycle(EventKind.REFRESH_COMPLETED, "r2"),
    ):
        publisher.publish(item)
    await drain(publisher)

    assert kinds(leaving) == [
        (EventKind.REFRESH_STARTED, "r1", None),
        (EventKind.REFRESH_FAILED, "r1", None),
    ]
    assert kinds(staying) == [
        (EventKind.REFRESH_STARTED, "r1", None),
        (EventKind.DISCOVERY_STARTED, "r1", None),
        (EventKind.REFRESH_STARTED, "r2", None),
        (EventKind.REFRESH_FAILED, "r1", None),
        (EventKind.REFRESH_COMPLETED, "r2", None),
    ]


async def test_a_resubscribed_or_new_handler_receives_each_lifecycle_end_once() -> None:
    publisher = AsyncEventPublisher()
    first, second = EventRecorder(), EventRecorder()
    publisher.subscribe(first)
    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r"))
    publisher.unsubscribe(first)
    publisher.subscribe(second)
    publisher.subscribe(first)

    await deliver(publisher, lifecycle(EventKind.REFRESH_COMPLETED, "r"))

    assert kinds(first) == [
        (EventKind.REFRESH_STARTED, "r", None),
        (EventKind.REFRESH_COMPLETED, "r", None),
    ]
    assert kinds(second) == [(EventKind.REFRESH_COMPLETED, "r", None)]


async def test_a_refresh_end_reaches_handlers_that_saw_only_a_resource_start() -> None:
    publisher = AsyncEventPublisher()
    early, late = EventRecorder(), EventRecorder()
    publisher.subscribe(early)
    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r"))
    publisher.subscribe(late)
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "a"))
    await deliver(publisher, lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "a"))
    publisher.unsubscribe(early)
    publisher.unsubscribe(late)

    await deliver(publisher, lifecycle(EventKind.REFRESH_CANCELLED, "r"))

    assert kinds(late) == [
        (EventKind.RESOURCE_REFRESH_STARTED, "r", "a"),
        (EventKind.RESOURCE_REFRESH_STARTED, "r", "a"),
        (EventKind.REFRESH_CANCELLED, "r", None),
    ]
    assert kinds(early)[-1] == (EventKind.REFRESH_CANCELLED, "r", None)
    assert publisher._recipients == {}


async def test_a_start_undelivered_before_the_last_unsubscribe_owes_no_end() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    publisher.subscribe(recorder)
    publisher.publish(lifecycle(EventKind.REFRESH_STARTED, "r"))
    publisher.unsubscribe(recorder)

    publisher.publish(lifecycle(EventKind.REFRESH_COMPLETED, "r"))
    await drain(publisher)

    assert recorder.events == []
    assert publisher.queued == 0
    assert publisher.has_subscribers is False


async def test_an_interrupted_lifecycle_end_is_redelivered_after_unsubscribing() -> (
    None
):
    publisher = AsyncEventPublisher()
    entered, release = asyncio.Event(), asyncio.Event()
    calls: list[EventKind] = []

    async def gate(item: RefreshEvent) -> None:
        calls.append(item.kind)
        if item.kind is EventKind.REFRESH_COMPLETED and not release.is_set():
            entered.set()
            await asyncio.Event().wait()

    publisher.subscribe(gate)
    await deliver(publisher, lifecycle(EventKind.REFRESH_STARTED, "r"))
    publisher.unsubscribe(gate)
    publisher.publish(lifecycle(EventKind.REFRESH_COMPLETED, "r"))
    await entered.wait()
    dispatcher = publisher._dispatcher
    assert dispatcher is not None
    dispatcher.cancel()
    await asyncio.wait((dispatcher,))
    assert publisher.has_subscribers is False
    release.set()

    await drain(publisher)

    assert calls == [
        EventKind.REFRESH_STARTED,
        EventKind.REFRESH_COMPLETED,
        EventKind.REFRESH_COMPLETED,
    ]
    assert publisher._owed == 0


async def test_lifecycles_left_open_by_the_last_unsubscribe_are_closed() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    recorder = EventRecorder()
    publisher.subscribe(recorder)

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        await publisher.drain()
        assert publisher.open_lifecycles == 2
        publisher.unsubscribe(recorder)

    engine = RefreshEngine(FakeSource({"a": 1}), operation, events=publisher)
    await engine.refresh()
    await engine.close()
    await drain(publisher)

    assert publisher.open_lifecycles == 0
    assert publisher._lifecycles == {}
    assert publisher.has_subscribers is False
    delivered = [item.kind for item in recorder.events]
    assert delivered[0] is EventKind.REFRESH_STARTED
    assert delivered[-2:] == [
        EventKind.RESOURCE_REFRESH_COMPLETED,
        EventKind.REFRESH_COMPLETED,
    ]
    assert EventKind.RESOURCE_REFRESH_STARTED in delivered


async def test_engine_events_share_one_lifecycle_identity_per_refresh() -> None:
    publisher = AsyncEventPublisher()
    recorder = EventRecorder()
    publisher.subscribe(recorder)

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        return None

    engine = RefreshEngine(FakeSource({"a": 1, "b": 2}), operation, events=publisher)
    await engine.refresh()
    await engine.refresh()
    await engine.close()
    await drain(publisher)

    identities: dict[str | None, set[int]] = {}
    for item in recorder.events:
        assert item._lifecycle is not None
        identities.setdefault(item.refresh_id, set()).add(id(item._lifecycle))
    assert len(identities) == 2
    assert all(len(tokens) == 1 for tokens in identities.values())
    first, second = recorder.events[0], recorder.events[-1]
    assert first._lifecycle is not second._lifecycle
    assert dataclasses.replace(first)._lifecycle is first._lifecycle
    assert first == dataclasses.replace(first, _lifecycle=None)


async def test_events_without_a_tracked_lifecycle_are_not_tracked() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    publisher.subscribe(EventRecorder())

    publisher.publish(event(EventKind.REFRESH_STARTED))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "unknown", "a"))
    publisher.publish(lifecycle(EventKind.REFRESH_STARTED, "r"))
    publisher.publish(lifecycle(EventKind.REFRESH_STARTED, "r"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "a"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_STARTED, "r", "a"))
    publisher.publish(event(EventKind.REFRESH_COMPLETED))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_SKIPPED, "r", "b"))
    publisher.publish(lifecycle(EventKind.RESOURCE_REFRESH_SKIPPED, "other", "a"))
    publisher.publish(lifecycle(EventKind.DISCOVERY_STARTED, "r"))
    assert publisher._tracked == 2
    publisher.publish(lifecycle(EventKind.REFRESH_COMPLETED, "r"))
    await drain(publisher)

    assert publisher._lifecycles == {}
    assert publisher._tracked == 0
