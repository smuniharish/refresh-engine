from __future__ import annotations

from typing import Any

from structlog.testing import capture_logs

from refresh_engine import (
    EventKind,
    EventPublisher,
    EventPublisherABC,
    InMemoryMetrics,
    MetricsEventHandler,
    RefreshEngine,
    RefreshEvent,
    StructlogEventHandler,
)
from tests.support import EventRecorder, FakeSource, RecordingOperation


def recorded() -> tuple[EventPublisher, EventRecorder]:
    events = EventPublisher()
    recorder = EventRecorder()
    events.subscribe(recorder)
    return events, recorder


async def test_a_refresh_emits_its_lifecycle_in_order() -> None:
    events, recorder = recorded()
    source = FakeSource({"a": 1, "b": 2}, dependencies={"b": {"a"}})
    async with RefreshEngine(source, RecordingOperation(), events=events) as engine:
        result = await engine.refresh(correlation_id="request-7")
    assert recorder.kinds() == [
        EventKind.REFRESH_STARTED,
        EventKind.DISCOVERY_STARTED,
        EventKind.DISCOVERY_COMPLETED,
        EventKind.SNAPSHOT_STARTED,
        EventKind.SNAPSHOT_COMPLETED,
        EventKind.DETECTION_COMPLETED,
        EventKind.PLANNING_COMPLETED,
        EventKind.EXECUTION_STARTED,
        EventKind.RESOURCE_REFRESH_STARTED,
        EventKind.RESOURCE_REFRESH_COMPLETED,
        EventKind.RESOURCE_REFRESH_STARTED,
        EventKind.RESOURCE_REFRESH_COMPLETED,
        EventKind.EXECUTION_COMPLETED,
        EventKind.STATE_PERSISTED,
        EventKind.REFRESH_COMPLETED,
    ]
    assert {event.refresh_id for event in recorder.events} == {result.refresh_id}
    assert {event.correlation_id for event in recorder.events} == {"request-7"}
    assert [
        event.resource_id for event in recorder.of(EventKind.RESOURCE_REFRESH_STARTED)
    ] == ["a", "b"]
    assert recorder.attributes(EventKind.REFRESH_STARTED) == {
        "mode": "incremental",
        "trigger": "manual",
    }
    discovery = recorder.attributes(EventKind.DISCOVERY_COMPLETED)
    assert (discovery["discovered"], discovery["complete"]) == (2, True)
    snapshot = recorder.attributes(EventKind.SNAPSHOT_COMPLETED)
    assert (snapshot["snapshotted"], snapshot["cheap_matches"], snapshot["failed"]) == (
        2,
        0,
        0,
    )
    assert recorder.attributes(EventKind.SNAPSHOT_STARTED) == {"resources": 2}
    assert recorder.attributes(EventKind.DETECTION_COMPLETED) == {
        "added": 2,
        "modified": 0,
        "dependency_changed": 0,
        "unchanged": 0,
        "deleted": 0,
    }
    assert recorder.attributes(EventKind.PLANNING_COMPLETED) == {
        "refresh": 2,
        "delete": 0,
        "impacted": 0,
    }
    assert recorder.attributes(EventKind.EXECUTION_STARTED) == {"operations": 2}
    completed = recorder.of(EventKind.RESOURCE_REFRESH_COMPLETED)[0].attributes
    assert (completed["action"], completed["attempts"]) == ("refresh", 1)
    execution = recorder.attributes(EventKind.EXECUTION_COMPLETED)
    assert (execution["succeeded"], execution["failed"]) == (2, 0)
    persisted = recorder.attributes(EventKind.STATE_PERSISTED)
    assert (persisted["written"], persisted["deleted"]) == (2, 0)
    terminal = recorder.attributes(EventKind.REFRESH_COMPLETED)
    assert terminal["status"] == "success"
    assert (terminal["mode"], terminal["trigger"]) == ("incremental", "manual")
    assert (terminal["discovered"], terminal["added"], terminal["refreshed"]) == (
        2,
        2,
        2,
    )
    assert (terminal["skipped"], terminal["failed"]) == (0, 0)
    assert terminal["duration"] == result.duration


async def test_request_metadata_is_attached_to_the_started_event() -> None:
    events, recorder = recorded()
    async with RefreshEngine(
        FakeSource({}), RecordingOperation(), events=events
    ) as engine:
        await engine.refresh(metadata={"tenant": "acme"})
    assert recorder.attributes(EventKind.REFRESH_STARTED) == {
        "mode": "incremental",
        "trigger": "manual",
        "metadata": {"tenant": "acme"},
    }


async def test_a_refresh_without_changes_persists_nothing() -> None:
    events, recorder = recorded()
    async with RefreshEngine(
        FakeSource({"a": 1}), RecordingOperation(), events=events
    ) as engine:
        await engine.refresh()
        recorder.events.clear()
        await engine.refresh()
    kinds = recorder.kinds()
    assert EventKind.EXECUTION_STARTED not in kinds
    assert EventKind.STATE_PERSISTED not in kinds
    assert kinds[-1] is EventKind.REFRESH_COMPLETED


async def test_failure_events_carry_the_exception() -> None:
    events, recorder = recorded()
    error = RuntimeError("write rejected")
    operation = RecordingOperation(failures={"a": error})
    async with RefreshEngine(FakeSource({"a": 1}), operation, events=events) as engine:
        await engine.refresh()
    (failed,) = recorder.of(EventKind.RESOURCE_REFRESH_FAILED)
    assert failed.error is error
    assert failed.attributes["error_type"] == "RuntimeError"
    assert failed.attributes["phase"] == "execution"
    (terminal,) = recorder.of(EventKind.REFRESH_FAILED)
    assert terminal.attributes["status"] == "failed"
    assert terminal.error is None


async def test_refresh_failed_event_carries_the_refresh_level_exception() -> None:
    events, recorder = recorded()
    error = ConnectionError("offline")
    async with RefreshEngine(
        FakeSource({}, discovery_error=error), RecordingOperation(), events=events
    ) as engine:
        await engine.refresh()
    (terminal,) = recorder.of(EventKind.REFRESH_FAILED)
    assert terminal.error is error


async def test_events_are_not_built_without_subscribers() -> None:
    class CountingPublisher(EventPublisherABC):
        def __init__(self) -> None:
            self.published = 0

        @property
        def has_subscribers(self) -> bool:
            return False

        def subscribe(self, handler: Any) -> None: ...

        def unsubscribe(self, handler: Any) -> None: ...

        def publish(self, event: RefreshEvent) -> None:
            self.published += 1

        async def drain(self) -> None:
            return None

    publisher = CountingPublisher()
    operation = RecordingOperation(failures={"a": RuntimeError("x")})
    async with RefreshEngine(
        FakeSource({"a": 1, "b": 1}), operation, events=publisher
    ) as engine:
        await engine.refresh()
    assert publisher.published == 0


async def test_structlog_handler_logs_refreshes_end_to_end() -> None:
    events = EventPublisher()
    events.subscribe(StructlogEventHandler())
    with capture_logs() as logs:
        async with RefreshEngine(
            FakeSource({"a": 1}), RecordingOperation(), events=events
        ) as engine:
            result = await engine.refresh(metadata={"api_key": "secret"})
    completed = [log for log in logs if log["event"] == "refresh_completed"]
    assert len(completed) == 1
    assert completed[0]["refresh_id"] == result.refresh_id
    assert completed[0]["status"] == "success"
    assert completed[0]["log_level"] == "info"
    assert all("secret" not in str(log) for log in logs)


async def test_metrics_handler_counts_refreshes_end_to_end() -> None:
    events = EventPublisher()
    metrics = InMemoryMetrics()
    events.subscribe(MetricsEventHandler(metrics))
    async with RefreshEngine(
        FakeSource({"a": 1, "b": 2}), RecordingOperation(), events=events
    ) as engine:
        await engine.refresh()
    counters, observations = metrics.snapshot()
    assert (
        counters[
            "refresh_engine.refreshes{mode=incremental,status=success,trigger=manual}"
        ]
        == 1
    )
    assert counters["refresh_engine.resources.detected{change=added}"] == 2
    assert counters["refresh_engine.operations{action=refresh,outcome=success}"] == 2
    assert (
        len(
            observations[
                "refresh_engine.refresh.duration{mode=incremental,status=success}"
            ]
        )
        == 1
    )
