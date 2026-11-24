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
    RefreshEvent,
)
from tests.support import EventRecorder

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


async def test_drain_restarts_delivery_after_the_dispatcher_was_cancelled() -> None:
    publisher = AsyncEventPublisher(capacity=10)
    started = asyncio.Event()
    seen: list[str | None] = []

    async def handler(item: RefreshEvent) -> None:
        if item.resource_id == "0" and not started.is_set():
            started.set()
            await asyncio.Event().wait()
        seen.append(item.resource_id)

    publisher.subscribe(handler)
    for index in range(3):
        publisher.publish(resource_event(index))
    await started.wait()
    dispatcher = publisher._dispatcher
    assert dispatcher is not None
    dispatcher.cancel()
    await asyncio.wait((dispatcher,))
    assert publisher.queued == 2

    await drain(publisher)

    assert seen == ["1", "2"]
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
