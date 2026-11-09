"""Tests for the optional OpenTelemetry adapters."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

try:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import StatusCode, TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from refresh_engine import EventKind, RefreshEvent
    from refresh_engine.otel import OpenTelemetryEventHandler, OpenTelemetryMetricsSink
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
