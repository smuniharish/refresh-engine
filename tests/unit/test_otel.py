"""Tests for the optional OpenTelemetry adapters."""

from __future__ import annotations

import asyncio
import gc
from datetime import UTC, datetime

import pytest

try:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import (
        ReadableSpan,
        Span,
        SpanProcessor,
        StatusCode,
        TracerProvider,
    )
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from refresh_engine import (
        AsyncEventPublisher,
        DiscoveryResult,
        EventKind,
        PlanAction,
        RefreshConfig,
        RefreshEngine,
        RefreshEvent,
        RefreshRequest,
        RefreshStatus,
        Resource,
        ResourceSnapshot,
        ResourceSource,
        TimeoutConfig,
    )
    from refresh_engine._internal.values import Lifecycle
    from refresh_engine.otel import OpenTelemetryEventHandler, OpenTelemetryMetricsSink
    from tests.support import EventRecorder
except ModuleNotFoundError as error:
    if error.name is None or not error.name.startswith("opentelemetry"):
        raise
    pytest.skip("install the otel extra to run these tests", allow_module_level=True)

TIMESTAMP = datetime(2025, 1, 1, tzinfo=UTC)


async def test_metrics_sink_reuses_instruments_and_exports_attributes() -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    sink = OpenTelemetryMetricsSink(provider.get_meter("refresh-engine-tests"))

    await sink.increment("refresh.count")
    await sink.increment("refresh.count", 2, {"mode": "full"})
    await sink.observe("refresh.duration", 1.25)
    await sink.observe("refresh.duration", 2.5, {"mode": "full"})

    data = reader.get_metrics_data()
    assert data is not None
    assert data.resource_metrics
    assert provider.shutdown() is None


async def test_event_handler_exports_parented_spans_and_metrics() -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    handler = OpenTelemetryEventHandler(
        tracer_provider.get_tracer("refresh-engine-tests"),
        meter_provider.get_meter("refresh-engine-tests"),
        prefix="test.refresh",
    )

    await handler(
        RefreshEvent(
            EventKind.REFRESH_STARTED,
            refresh_id="refresh-1",
            correlation_id="correlation-1",
            timestamp=TIMESTAMP,
            attributes={
                "mode": "incremental",
                "trigger": "manual",
                "ignored": ["not", "scalar"],
            },
        )
    )
    await handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_STARTED,
            refresh_id="refresh-1",
            resource_id="resource-1",
            timestamp=TIMESTAMP,
            attributes={"action": "refresh"},
        )
    )
    await handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_COMPLETED,
            refresh_id="refresh-1",
            resource_id="resource-1",
            timestamp=TIMESTAMP,
            attributes={
                "action": "refresh",
                "attempts": 1,
                "duration": 0.25,
                "reason": True,
                "ignored": {"private": "value"},
            },
        )
    )
    await handler(
        RefreshEvent(
            EventKind.REFRESH_COMPLETED,
            refresh_id="refresh-1",
            timestamp=TIMESTAMP,
            attributes={
                "status": "success",
                "mode": "incremental",
                "trigger": "manual",
                "duration": 0.5,
                "refreshed": 1,
                "deleted": 0,
                "failed": 0,
            },
        )
    )

    spans = exporter.get_finished_spans()
    refresh = next(span for span in spans if span.name == "refresh_engine.refresh")
    resource = next(span for span in spans if span.name == "refresh_engine.resource")
    assert refresh.name == "refresh_engine.refresh"
    assert resource.name == "refresh_engine.resource"
    assert resource.parent is not None
    assert refresh.context is not None
    assert refresh.attributes is not None
    assert resource.attributes is not None
    assert resource.parent.span_id == refresh.context.span_id
    assert refresh.attributes["refresh_engine.refresh.id"] == "refresh-1"
    assert "refresh_engine.correlation_id" not in refresh.attributes
    assert "refresh_engine.resource.id" not in resource.attributes
    assert resource.attributes["refresh_engine.reason"] is True
    assert "refresh_engine.ignored" not in refresh.attributes
    assert "refresh_engine.ignored" not in resource.attributes
    assert metric_reader.get_metrics_data() is not None
    tracer_provider.shutdown()
    meter_provider.shutdown()


async def test_event_handler_records_identifiers_when_enabled() -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    meter_provider = MeterProvider(metric_readers=[InMemoryMetricReader()])
    handler = OpenTelemetryEventHandler(
        tracer_provider.get_tracer("refresh-engine-tests"),
        meter_provider.get_meter("refresh-engine-tests"),
        record_identifiers=True,
    )

    for event in (
        RefreshEvent(
            EventKind.REFRESH_STARTED,
            refresh_id="refresh-1",
            correlation_id="correlation-1",
            timestamp=TIMESTAMP,
        ),
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_STARTED,
            refresh_id="refresh-1",
            resource_id="resource-1",
            timestamp=TIMESTAMP,
        ),
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_COMPLETED,
            refresh_id="refresh-1",
            resource_id="resource-1",
            timestamp=TIMESTAMP,
        ),
        RefreshEvent(
            EventKind.REFRESH_COMPLETED, refresh_id="refresh-1", timestamp=TIMESTAMP
        ),
    ):
        await handler(event)

    spans = {span.name: span for span in exporter.get_finished_spans()}
    refresh_attributes = spans["refresh_engine.refresh"].attributes
    resource_attributes = spans["refresh_engine.resource"].attributes
    assert refresh_attributes is not None
    assert resource_attributes is not None
    assert refresh_attributes["refresh_engine.correlation_id"] == "correlation-1"
    assert resource_attributes["refresh_engine.resource.id"] == "resource-1"
    tracer_provider.shutdown()
    meter_provider.shutdown()


@pytest.mark.parametrize(
    ("resource_kind", "refresh_kind", "expected_status"),
    [
        (EventKind.RESOURCE_REFRESH_FAILED, EventKind.REFRESH_FAILED, StatusCode.ERROR),
        (
            EventKind.RESOURCE_REFRESH_SKIPPED,
            EventKind.REFRESH_SKIPPED,
            StatusCode.UNSET,
        ),
    ],
)
async def test_event_handler_sets_failure_and_skipped_status(
    resource_kind: EventKind,
    refresh_kind: EventKind,
    expected_status: StatusCode,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    handler = OpenTelemetryEventHandler(
        provider.get_tracer("refresh-engine-tests"),
        meter_provider.get_meter("refresh-engine-tests"),
    )

    await handler(RefreshEvent(EventKind.REFRESH_STARTED, refresh_id="refresh-2"))
    await handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_STARTED,
            refresh_id="refresh-2",
            resource_id="resource-2",
        )
    )
    await handler(
        RefreshEvent(
            resource_kind,
            refresh_id="refresh-2",
            resource_id="resource-2",
            attributes={
                "action": "refresh",
                "attempts": 2,
                "duration": 0.5,
                "phase": "execution",
                "error_type": "RuntimeError",
                "reason": "policy",
            },
        )
    )
    await handler(
        RefreshEvent(
            refresh_kind,
            refresh_id="refresh-2",
            attributes={
                "status": "failed",
                "mode": "full",
                "trigger": "manual",
                "duration": 1.0,
                "refreshed": 0,
                "deleted": 0,
                "failed": 1,
            },
        )
    )

    spans = exporter.get_finished_spans()
    assert all(span.status.status_code is expected_status for span in spans)
    provider.shutdown()
    meter_provider.shutdown()


async def test_event_handler_covers_cancelled_and_unmatched_events() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    meter_provider = MeterProvider(metric_readers=[InMemoryMetricReader()])
    handler = OpenTelemetryEventHandler(
        provider.get_tracer("refresh-engine-tests"),
        meter_provider.get_meter("refresh-engine-tests"),
    )

    await handler(RefreshEvent(EventKind.REFRESH_STARTED))
    await handler(RefreshEvent(EventKind.DISCOVERY_STARTED))
    await handler(RefreshEvent(EventKind.RESOURCE_REFRESH_STARTED))
    await handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_COMPLETED,
            resource_id="resource-without-refresh",
        )
    )
    await handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_FAILED,
            refresh_id="missing",
            resource_id="resource-3",
        )
    )
    await handler(RefreshEvent(EventKind.REFRESH_COMPLETED))
    await handler(RefreshEvent(EventKind.REFRESH_FAILED, refresh_id="missing"))
    await handler(RefreshEvent(EventKind.REFRESH_STARTED, refresh_id="refresh-3"))
    await handler(
        RefreshEvent(
            EventKind.REFRESH_CANCELLED,
            refresh_id="refresh-3",
            attributes={"status": "cancelled"},
        )
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code is StatusCode.ERROR
    provider.shutdown()
    meter_provider.shutdown()


def telemetry(
    meter_provider: MeterProvider | None = None,
) -> tuple[InMemorySpanExporter, TracerProvider, OpenTelemetryEventHandler]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    meters = meter_provider or MeterProvider(metric_readers=[InMemoryMetricReader()])
    handler = OpenTelemetryEventHandler(
        provider.get_tracer("refresh-engine-tests"),
        meters.get_meter("refresh-engine-tests"),
    )
    return exporter, provider, handler


def tracked(handler: OpenTelemetryEventHandler) -> tuple[int, int]:
    open_resources = sum(len(spans) for spans in handler._resource_spans.values())
    return len(handler._refresh_spans), open_resources


@pytest.mark.parametrize(
    ("terminal", "description"),
    [
        (EventKind.REFRESH_FAILED, "the refresh failed before the operation finished"),
        (
            EventKind.REFRESH_CANCELLED,
            "the refresh was cancelled before the operation finished",
        ),
        (
            EventKind.REFRESH_COMPLETED,
            "the refresh finished before the operation reported",
        ),
        (
            EventKind.REFRESH_SKIPPED,
            "the refresh was skipped before the operation finished",
        ),
    ],
)
async def test_terminal_refresh_event_ends_outstanding_resource_spans(
    terminal: EventKind, description: str
) -> None:
    exporter, provider, handler = telemetry()

    await handler(RefreshEvent(EventKind.REFRESH_STARTED, refresh_id="r"))
    for resource_id in ("a", "b"):
        await handler(
            RefreshEvent(
                EventKind.RESOURCE_REFRESH_STARTED,
                refresh_id="r",
                resource_id=resource_id,
            )
        )
    await handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_COMPLETED, refresh_id="r", resource_id="a"
        )
    )
    await handler(RefreshEvent(terminal, refresh_id="r"))

    spans = exporter.get_finished_spans()
    assert len(spans) == 3
    refresh = next(span for span in spans if span.name == "refresh_engine.refresh")
    interrupted = [
        span
        for span in spans
        if span.attributes
        and span.attributes.get("refresh_engine.resource.interrupted")
    ]
    assert len(interrupted) == 1
    assert interrupted[0].status.status_code is StatusCode.ERROR
    assert interrupted[0].status.description == description
    assert interrupted[0].parent is not None
    assert refresh.context is not None
    assert interrupted[0].parent.span_id == refresh.context.span_id
    assert tracked(handler) == (0, 0)
    provider.shutdown()


async def test_duplicate_and_late_events_are_ignored() -> None:
    exporter, provider, handler = telemetry()
    started = RefreshEvent(EventKind.REFRESH_STARTED, refresh_id="r")
    resource = RefreshEvent(
        EventKind.RESOURCE_REFRESH_STARTED, refresh_id="r", resource_id="a"
    )

    await handler(started)
    await handler(started)
    await handler(resource)
    await handler(resource)
    await handler(RefreshEvent(EventKind.REFRESH_COMPLETED, refresh_id="r"))
    await handler(RefreshEvent(EventKind.REFRESH_COMPLETED, refresh_id="r"))
    await handler(started)
    await handler(resource)
    await handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_COMPLETED, refresh_id="r", resource_id="a"
        )
    )

    assert len(exporter.get_finished_spans()) == 2
    assert tracked(handler) == (0, 0)
    provider.shutdown()


async def test_resource_events_without_an_open_refresh_span_start_nothing() -> None:
    exporter, provider, handler = telemetry()

    await handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_STARTED, refresh_id="r", resource_id="a"
        )
    )
    await handler(RefreshEvent(EventKind.REFRESH_FAILED, refresh_id="r"))

    assert exporter.get_finished_spans() == ()
    assert handler._resource_spans == {}
    assert tracked(handler) == (0, 0)
    provider.shutdown()


async def test_finished_refresh_memory_is_bounded() -> None:
    _, provider, handler = telemetry()

    for index in range(1100):
        await handler(RefreshEvent(EventKind.REFRESH_COMPLETED, refresh_id=str(index)))

    assert len(handler._finished) == 1024
    assert "0" not in handler._finished
    provider.shutdown()


async def test_span_cleanup_runs_when_a_metric_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter, provider, handler = telemetry()

    async def broken(event: RefreshEvent) -> None:
        raise RuntimeError("metrics backend down")

    await handler(RefreshEvent(EventKind.REFRESH_STARTED, refresh_id="r"))
    monkeypatch.setattr(handler, "_metrics", broken)
    with pytest.raises(RuntimeError, match="metrics backend down"):
        await handler(
            RefreshEvent(
                EventKind.RESOURCE_REFRESH_STARTED, refresh_id="r", resource_id="a"
            )
        )
    with pytest.raises(RuntimeError, match="metrics backend down"):
        await handler(RefreshEvent(EventKind.REFRESH_CANCELLED, refresh_id="r"))

    assert len(exporter.get_finished_spans()) == 2
    assert tracked(handler) == (0, 0)
    provider.shutdown()


async def test_concurrent_refreshes_are_tracked_independently() -> None:
    exporter, provider, handler = telemetry()

    for refresh_id in ("r1", "r2"):
        await handler(RefreshEvent(EventKind.REFRESH_STARTED, refresh_id=refresh_id))
        await handler(
            RefreshEvent(
                EventKind.RESOURCE_REFRESH_STARTED,
                refresh_id=refresh_id,
                resource_id="shared",
            )
        )
    await handler(RefreshEvent(EventKind.REFRESH_CANCELLED, refresh_id="r1"))

    assert len(exporter.get_finished_spans()) == 2
    assert tracked(handler) == (1, 1)
    await handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_COMPLETED, refresh_id="r2", resource_id="shared"
        )
    )
    await handler(RefreshEvent(EventKind.REFRESH_COMPLETED, refresh_id="r2"))

    spans = exporter.get_finished_spans()
    assert len(spans) == 4
    r2_resource = next(
        span
        for span in spans
        if span.name == "refresh_engine.resource"
        and span.attributes
        and span.attributes["refresh_engine.refresh.id"] == "r2"
    )
    assert r2_resource.status.status_code is StatusCode.UNSET
    assert tracked(handler) == (0, 0)
    provider.shutdown()


class Blocking(ResourceSource):
    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult([Resource("a"), Resource("b")])

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(resource.resource_id, content=resource.resource_id)


async def hang(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
) -> None:
    await asyncio.Event().wait()


async def test_engine_overall_timeout_leaves_no_open_resource_spans() -> None:
    exporter, provider, handler = telemetry()
    events = AsyncEventPublisher()
    events.subscribe(handler)
    engine = RefreshEngine(
        Blocking(),
        hang,
        config=RefreshConfig(timeout=TimeoutConfig(overall=0.05)),
        events=events,
    )

    result = await engine.refresh()
    await engine.close()

    assert result.status is RefreshStatus.FAILED
    resources = [
        span
        for span in exporter.get_finished_spans()
        if span.name == "refresh_engine.resource"
    ]
    assert len(resources) == 2
    assert all(span.status.status_code is StatusCode.ERROR for span in resources)
    assert tracked(handler) == (0, 0)
    provider.shutdown()


async def test_engine_cancellation_leaves_no_open_resource_spans() -> None:
    exporter, provider, handler = telemetry()
    events = AsyncEventPublisher()
    running = asyncio.Event()

    async def notice(event: RefreshEvent) -> None:
        if event.kind is EventKind.RESOURCE_REFRESH_STARTED:
            running.set()

    events.subscribe(handler)
    events.subscribe(notice)
    engine = RefreshEngine(Blocking(), hang, events=events)

    task = asyncio.create_task(engine.refresh())
    await running.wait()
    await engine.close(cancel=True)
    result = await task

    assert result.status is RefreshStatus.CANCELLED
    resources = [
        span
        for span in exporter.get_finished_spans()
        if span.name == "refresh_engine.resource"
    ]
    assert resources
    assert all(
        span.attributes and span.attributes["refresh_engine.resource.interrupted"]
        for span in resources
    )
    assert tracked(handler) == (0, 0)
    provider.shutdown()


class SpanLedger(SpanProcessor):
    """Counts how often each span is started and ended."""

    def __init__(self) -> None:
        self.started: dict[int, str] = {}
        self.ended: dict[int, int] = {}

    def on_start(self, span: Span, parent_context: object = None) -> None:
        assert span.context is not None
        self.started[span.context.span_id] = span.name

    def on_end(self, span: ReadableSpan) -> None:
        assert span.context is not None
        span_id = span.context.span_id
        self.ended[span_id] = self.ended.get(span_id, 0) + 1

    def assert_balanced(self) -> None:
        assert self.started.keys() == self.ended.keys()
        assert set(self.ended.values()) <= {1}


def ledger_telemetry() -> tuple[SpanLedger, TracerProvider, OpenTelemetryEventHandler]:
    ledger = SpanLedger()
    provider = TracerProvider()
    provider.add_span_processor(ledger)
    meters = MeterProvider(metric_readers=[InMemoryMetricReader()])
    handler = OpenTelemetryEventHandler(
        provider.get_tracer("refresh-engine-tests"),
        meters.get_meter("refresh-engine-tests"),
    )
    return ledger, provider, handler


def refresh_events(
    refresh_id: str, lifecycle: Lifecycle | None = None
) -> list[RefreshEvent]:
    return [
        RefreshEvent(
            EventKind.REFRESH_STARTED, refresh_id=refresh_id, _lifecycle=lifecycle
        ),
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_STARTED,
            refresh_id=refresh_id,
            resource_id="a",
            _lifecycle=lifecycle,
        ),
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_COMPLETED,
            refresh_id=refresh_id,
            resource_id="a",
            _lifecycle=lifecycle,
        ),
        RefreshEvent(
            EventKind.REFRESH_COMPLETED, refresh_id=refresh_id, _lifecycle=lifecycle
        ),
    ]


async def test_late_events_after_history_eviction_start_no_spans() -> None:
    ledger, provider, handler = ledger_telemetry()
    old = refresh_events("old")
    for item in old:
        await handler(item)
    for index in range(1100):
        await handler(RefreshEvent(EventKind.REFRESH_COMPLETED, refresh_id=str(index)))
    assert "old" not in handler._finished
    started = len(ledger.started)

    for item in old:
        await handler(item)

    assert len(ledger.started) == started == 2
    assert "old" not in handler._finished
    assert tracked(handler) == (0, 0)
    for item in refresh_events("new"):
        await handler(item)
    assert len(ledger.started) == 4
    assert "new" in handler._finished
    ledger.assert_balanced()
    provider.shutdown()


async def test_events_delivered_by_several_publishers_end_each_span_once() -> None:
    ledger, provider, handler = ledger_telemetry()
    publishers = [AsyncEventPublisher(), AsyncEventPublisher()]
    for publisher in publishers:
        publisher.subscribe(handler)

    for refresh_id in ("r1", "r2"):
        for item in refresh_events(refresh_id):
            for publisher in publishers:
                publisher.publish(item)
    for publisher in publishers:
        await publisher.drain()

    assert len(ledger.started) == 4
    ledger.assert_balanced()
    assert tracked(handler) == (0, 0)
    provider.shutdown()


async def test_a_delayed_start_from_another_publisher_is_traced() -> None:
    ledger, provider, handler = ledger_telemetry()
    slow, fast = AsyncEventPublisher(), AsyncEventPublisher()
    for publisher in (slow, fast):
        publisher.subscribe(handler)
    delayed = refresh_events("delayed", Lifecycle())
    replayed = refresh_events("replayed", Lifecycle())
    for item in replayed:
        fast.publish(item)
    await fast.drain()

    for index in range(1100):
        for item in refresh_events(f"other-{index}", Lifecycle()):
            fast.publish(item)
    await fast.drain()
    assert handler._finished == {}
    for item in delayed:
        slow.publish(item)
    slow.publish(delayed[0])
    for item in replayed:
        slow.publish(item)
    await slow.drain()

    assert sorted(ledger.started.values()).count("refresh_engine.refresh") == 1102
    ledger.assert_balanced()
    assert tracked(handler) == (0, 0)
    provider.shutdown()


async def test_a_new_lifecycle_of_a_finished_refresh_id_is_traced() -> None:
    ledger, provider, handler = ledger_telemetry()

    for _ in range(2):
        lifecycle = Lifecycle()
        for item in refresh_events("r", lifecycle):
            await handler(item)
            await handler(item)

    assert len(ledger.started) == 4
    ledger.assert_balanced()
    provider.shutdown()


async def test_events_without_lifecycle_identity_keep_the_bounded_fallback() -> None:
    ledger, provider, handler = ledger_telemetry()
    delayed = refresh_events("delayed")
    for index in range(1100):
        await handler(RefreshEvent(EventKind.REFRESH_COMPLETED, refresh_id=str(index)))

    for item in delayed:
        await handler(item)

    assert ledger.started == {}
    assert "delayed" not in handler._finished
    provider.shutdown()


async def test_finished_lifecycles_are_forgotten_with_their_events() -> None:
    ledger, provider, handler = ledger_telemetry()
    events = refresh_events("r", Lifecycle())
    for item in events:
        await handler(item)
    assert len(handler._finished_lifecycles) == 1

    del events, item
    gc.collect()

    assert len(handler._finished_lifecycles) == 0
    ledger.assert_balanced()
    provider.shutdown()


async def test_lifecycle_tracking_stays_bounded_over_many_refreshes() -> None:
    ledger, provider, handler = ledger_telemetry()

    for index in range(3000):
        for item in refresh_events(str(index)):
            await handler(item)

    assert len(handler._finished) == 1024
    assert handler._resource_spans == {}
    assert tracked(handler) == (0, 0)
    assert len(ledger.started) == 6000
    ledger.assert_balanced()
    provider.shutdown()


async def test_a_failing_span_operation_does_not_leave_other_spans_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, provider, handler = ledger_telemetry()
    await handler(RefreshEvent(EventKind.REFRESH_STARTED, refresh_id="r"))
    for resource_id in ("a", "b"):
        await handler(
            RefreshEvent(
                EventKind.RESOURCE_REFRESH_STARTED,
                refresh_id="r",
                resource_id=resource_id,
            )
        )
    spans = list(handler._resource_spans["r"].values())
    end = spans[1].end

    def broken_status(*args: object, **kwargs: object) -> None:
        raise RuntimeError("status rejected")

    def broken_end(end_time: int | None = None) -> None:
        end(end_time=end_time)
        raise RuntimeError("exporter rejected")

    monkeypatch.setattr(spans[0], "set_status", broken_status)
    monkeypatch.setattr(spans[1], "end", broken_end)

    with pytest.raises(RuntimeError, match="status rejected"):
        await handler(RefreshEvent(EventKind.REFRESH_CANCELLED, refresh_id="r"))
    await handler(RefreshEvent(EventKind.REFRESH_CANCELLED, refresh_id="r"))

    assert len(ledger.started) == 3
    ledger.assert_balanced()
    assert tracked(handler) == (0, 0)
    provider.shutdown()


async def test_unsubscribing_mid_refresh_still_ends_every_started_span() -> None:
    ledger, provider, handler = ledger_telemetry()
    events = AsyncEventPublisher()
    events.subscribe(handler)

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        await events.drain()
        if resource.resource_id == "1":
            events.unsubscribe(handler)

    engine = RefreshEngine(
        Mixed(3),
        operation,
        config=RefreshConfig(max_concurrency=1),
        events=events,
    )
    await engine.refresh()
    await engine.close()
    await events.drain()

    assert len(ledger.started) == 3
    ledger.assert_balanced()
    assert tracked(handler) == (0, 0)
    assert events.open_lifecycles == 0
    assert events.has_subscribers is False
    provider.shutdown()


class Mixed(ResourceSource):
    def __init__(self, count: int) -> None:
        self.count = count

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult([Resource(str(index)) for index in range(self.count)])

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(resource.resource_id, content=resource.resource_id)


async def test_saturated_concurrent_refreshes_leave_no_orphaned_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AsyncEventPublisher, "BOUNDARY_RESERVE", 2)
    ledger, provider, handler = ledger_telemetry()
    events = AsyncEventPublisher(capacity=64)
    release = asyncio.Event()
    blocked = asyncio.Event()

    async def gate(event: RefreshEvent) -> None:
        if not release.is_set():
            blocked.set()
            await release.wait()

    events.subscribe(gate)
    events.subscribe(handler)
    recorder = EventRecorder()
    events.subscribe(recorder)
    hanging = asyncio.Event()

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        mode = request.metadata["mode"]
        if mode == "fail" and resource.resource_id == "1":
            raise RuntimeError("boom")
        if mode in {"hang", "timeout"} and resource.resource_id == "0":
            await hanging.wait()

    def engine(mode: str) -> RefreshEngine:
        config = RefreshConfig(
            timeout=TimeoutConfig(overall=0.05 if mode == "timeout" else None)
        )
        return RefreshEngine(Mixed(4), operation, config=config, events=events)

    modes = ["ok", "fail", "hang", "timeout"] * 15
    engines = [engine(mode) for mode in modes]
    tasks = [
        asyncio.create_task(item.refresh(metadata={"mode": mode}))
        for item, mode in zip(engines, modes, strict=True)
    ]
    await blocked.wait()
    for item, mode in zip(engines, modes, strict=True):
        if mode == "hang":
            await item.close(cancel=True, wait=False)
    results = await asyncio.gather(*tasks)
    assert events.dropped > 0
    assert {result.status for result in results} == {
        RefreshStatus.SUCCESS,
        RefreshStatus.PARTIAL,
        RefreshStatus.CANCELLED,
        RefreshStatus.FAILED,
    }

    release.set()
    await events.drain()

    assert ledger.started
    ledger.assert_balanced()
    assert tracked(handler) == (0, 0)
    assert events._lifecycles == {}
    terminals = [
        item
        for item in recorder.events
        if item.kind.value.startswith("refresh_")
        and item.kind is not EventKind.REFRESH_STARTED
    ]
    started = [
        item for item in recorder.events if item.kind is EventKind.REFRESH_STARTED
    ]
    assert {item.refresh_id for item in started} <= {
        item.refresh_id for item in terminals
    }
    provider.shutdown()
