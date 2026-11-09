"""OpenTelemetry metrics and tracing adapters.

This module requires the ``otel`` extra. It uses only the supplied meter and
tracer; provider configuration and shutdown remain application-owned.
"""

from __future__ import annotations

from collections.abc import Mapping

from opentelemetry.context import get_current
from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.trace import Span, Status, StatusCode, Tracer, set_span_in_context

from refresh_engine.contracts import MetricsSink
from refresh_engine.events import EventHandler, EventKind, RefreshEvent
from refresh_engine.observability import MetricsEventHandler

__all__ = ["OpenTelemetryEventHandler", "OpenTelemetryMetricsSink"]


class OpenTelemetryMetricsSink(MetricsSink):
    """Write refresh-engine counters and observations to a supplied meter."""

    def __init__(self, meter: Meter) -> None:
        """Create a sink without configuring a global meter provider."""
        self._meter = meter
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}

    async def increment(
        self,
        name: str,
        value: int = 1,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        """Add a value to a counter, creating the instrument on first use."""
        counter = self._counters.get(name)
        if counter is None:
            counter = self._meter.create_counter(name)
            self._counters[name] = counter
        counter.add(value, attributes=dict(attributes or {}))

    async def observe(
        self,
        name: str,
        value: float,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        """Record a value in a histogram, creating the instrument on first use."""
        histogram = self._histograms.get(name)
        if histogram is None:
            histogram = self._meter.create_histogram(name, unit="s")
            self._histograms[name] = histogram
        histogram.record(value, attributes=dict(attributes or {}))


class OpenTelemetryEventHandler(EventHandler):
    """Export lifecycle metrics and one refresh/resource span per operation.

    Only bounded, non-identifying attributes are recorded by default; spans carry
    the generated refresh ID so related spans can be correlated. Set
    ``record_identifiers=True`` to also record resource and correlation IDs on
    spans, which may contain application data. Refresh metadata and error
    messages are never recorded. Provider and exporter lifecycle remain owned by
    the application.
    """

    def __init__(
        self,
        tracer: Tracer,
        meter: Meter,
        *,
        prefix: str = "refresh_engine",
        record_identifiers: bool = False,
    ) -> None:
        """Create an adapter around caller-owned telemetry instruments."""
        self._tracer = tracer
        self._record_identifiers = record_identifiers
        self._metrics = MetricsEventHandler(
            OpenTelemetryMetricsSink(meter), prefix=prefix
        )
        self._refresh_spans: dict[str, Span] = {}
        self._resource_spans: dict[tuple[str, str], Span] = {}

    async def __call__(self, event: RefreshEvent) -> None:
        """Translate one event to metrics and span lifecycle operations."""
        await self._metrics(event)
        if event.kind is EventKind.REFRESH_STARTED:
            self._start_refresh(event)
        elif event.kind is EventKind.RESOURCE_REFRESH_STARTED:
            self._start_resource(event)
        elif event.kind in (
            EventKind.RESOURCE_REFRESH_COMPLETED,
            EventKind.RESOURCE_REFRESH_FAILED,
            EventKind.RESOURCE_REFRESH_SKIPPED,
        ):
            self._finish_resource(event)
        elif event.kind in (
            EventKind.REFRESH_COMPLETED,
            EventKind.REFRESH_FAILED,
            EventKind.REFRESH_CANCELLED,
            EventKind.REFRESH_SKIPPED,
        ):
            self._finish_refresh(event)

    def _start_refresh(self, event: RefreshEvent) -> None:
        refresh_id = event.refresh_id
        if refresh_id is None:
            return
        parent = event._context.run(get_current)
        span = self._tracer.start_span(
            "refresh_engine.refresh",
            context=parent,
            start_time=_timestamp_ns(event),
        )
        span.set_attribute("refresh_engine.refresh.id", refresh_id)
        _set_attributes(span, event.attributes, ("mode", "trigger"))
        if self._record_identifiers and event.correlation_id is not None:
            span.set_attribute("refresh_engine.correlation_id", event.correlation_id)
        self._refresh_spans[refresh_id] = span

    def _start_resource(self, event: RefreshEvent) -> None:
        refresh_id, resource_id = event.refresh_id, event.resource_id
        if refresh_id is None or resource_id is None:
            return
        refresh = self._refresh_spans.get(refresh_id)
        parent_context = set_span_in_context(refresh) if refresh is not None else None
        span = self._tracer.start_span(
            "refresh_engine.resource",
            context=parent_context,
            start_time=_timestamp_ns(event),
        )
        if self._record_identifiers:
            span.set_attribute("refresh_engine.resource.id", resource_id)
        span.set_attribute("refresh_engine.refresh.id", refresh_id)
        _set_attributes(span, event.attributes, ("action",))
        self._resource_spans[(refresh_id, resource_id)] = span

    def _finish_resource(self, event: RefreshEvent) -> None:
        if event.refresh_id is None or event.resource_id is None:
            return
        span = self._resource_spans.pop((event.refresh_id, event.resource_id), None)
        if span is None:
            return
        _set_attributes(
            span,
            event.attributes,
            ("action", "attempts", "duration", "phase", "error_type", "reason"),
        )
        if event.kind is EventKind.RESOURCE_REFRESH_FAILED:
            span.set_status(Status(StatusCode.ERROR))
        span.end(end_time=_timestamp_ns(event))

    def _finish_refresh(self, event: RefreshEvent) -> None:
        if event.refresh_id is None:
            return
        span = self._refresh_spans.pop(event.refresh_id, None)
        if span is None:
            return
        _set_attributes(
            span,
            event.attributes,
            (
                "status",
                "mode",
                "trigger",
                "duration",
                "refreshed",
                "deleted",
                "failed",
            ),
        )
        if event.kind in (EventKind.REFRESH_FAILED, EventKind.REFRESH_CANCELLED):
            span.set_status(Status(StatusCode.ERROR))
        span.end(end_time=_timestamp_ns(event))


def _timestamp_ns(event: RefreshEvent) -> int:
    return int(event.timestamp.timestamp() * 1_000_000_000)


def _set_attributes(
    span: Span,
    values: Mapping[str, object],
    allowed: tuple[str, ...],
) -> None:
    for key in allowed:
        value = values.get(key)
        if isinstance(value, str | bool | int | float):
            span.set_attribute(f"refresh_engine.{key}", value)
