"""OpenTelemetry metrics and tracing adapters.

This module requires the ``otel`` extra. It uses only the supplied meter and
tracer; provider configuration and shutdown remain application-owned.
"""

from __future__ import annotations

import weakref
from collections import OrderedDict
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final

from opentelemetry.context import get_current
from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.trace import Span, Status, StatusCode, Tracer, set_span_in_context

from refresh_engine._internal.values import Lifecycle, strict_bool
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

    A terminal refresh event ends the refresh span and every resource span of
    that refresh that is still open, for example after a cancellation or an
    overall timeout interrupted the operation; those resource spans get an error
    status and the attribute ``refresh_engine.resource.interrupted``. Resource
    spans are only started while their refresh span is open. The default
    [`AsyncEventPublisher`][refresh_engine.AsyncEventPublisher] always delivers
    the event that closes a lifecycle whose start it delivered, even when its
    queue is full, so every span is ended exactly once.

    Duplicate, late, and out-of-order events never start or end a span twice.
    Events published by the engine carry an identity shared by every event of
    their refresh, and the handler remembers each finished refresh for as long as
    any of its events exists, so a delayed `REFRESH_STARTED` of an unfinished
    refresh is traced however many other refreshes finished meanwhile, even
    through other publishers, while one of a finished refresh never starts a
    span again. For events created by application code, which lack that
    identity, the handler remembers the last 1,024 finished refresh IDs, and
    ignores a `REFRESH_STARTED` created before the end of a refresh it has since
    forgotten; such a start can be ignored when it is delivered after more than
    1,024 other refreshes finished. Span bookkeeping runs even when recording a
    metric or ending another span fails.

    Raises:
        ConfigurationError: If `record_identifiers` is not a `bool`.
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
        self._record_identifiers = strict_bool(record_identifiers, "record_identifiers")
        self._metrics = MetricsEventHandler(
            OpenTelemetryMetricsSink(meter), prefix=prefix
        )
        self._refresh_spans: dict[str, Span] = {}
        self._resource_spans: dict[str, dict[str, Span]] = {}
        self._finished: OrderedDict[str, int] = OrderedDict()
        self._forgotten = -1
        self._finished_lifecycles: weakref.WeakSet[Lifecycle] = weakref.WeakSet()

    async def __call__(self, event: RefreshEvent) -> None:
        """Translate one event to metrics and span lifecycle operations."""
        try:
            await self._metrics(event)
        finally:
            self._trace(event)

    def _trace(self, event: RefreshEvent) -> None:
        kind = event.kind
        if kind is EventKind.REFRESH_STARTED:
            self._start_refresh(event)
        elif kind is EventKind.RESOURCE_REFRESH_STARTED:
            self._start_resource(event)
        elif kind in _RESOURCE_TERMINAL:
            self._finish_resource(event)
        elif kind in _REFRESH_TERMINAL:
            self._finish_refresh(event)

    def _start_refresh(self, event: RefreshEvent) -> None:
        refresh_id = event.refresh_id
        if refresh_id is None or refresh_id in self._refresh_spans:
            return
        lifecycle = event._lifecycle
        if lifecycle is not None:
            if lifecycle in self._finished_lifecycles:
                return
        elif refresh_id in self._finished or event._sequence <= self._forgotten:
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
        if refresh is None:
            return
        spans = self._resource_spans.setdefault(refresh_id, {})
        if resource_id in spans:
            return
        span = self._tracer.start_span(
            "refresh_engine.resource",
            context=set_span_in_context(refresh),
            start_time=_timestamp_ns(event),
        )
        if self._record_identifiers:
            span.set_attribute("refresh_engine.resource.id", resource_id)
        span.set_attribute("refresh_engine.refresh.id", refresh_id)
        _set_attributes(span, event.attributes, ("action",))
        spans[resource_id] = span

    def _finish_resource(self, event: RefreshEvent) -> None:
        refresh_id, resource_id = event.refresh_id, event.resource_id
        if refresh_id is None or resource_id is None:
            return
        spans = self._resource_spans.get(refresh_id)
        span = spans.pop(resource_id, None) if spans is not None else None
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
        refresh_id = event.refresh_id
        if refresh_id is None:
            return
        lifecycle = event._lifecycle
        if lifecycle is not None:
            if lifecycle in self._finished_lifecycles:
                return
            self._finished_lifecycles.add(lifecycle)
        elif not self._remember(refresh_id, event._sequence):
            return
        end_time = _timestamp_ns(event)
        status = _INTERRUPTED_STATUS[event.kind]
        failures: list[Exception] = []

        def interrupt(span: Span) -> None:
            span.set_attribute("refresh_engine.resource.interrupted", True)
            span.set_status(status)

        def complete(span: Span) -> None:
            _set_attributes(span, event.attributes, _REFRESH_ATTRIBUTES)
            if event.kind in (EventKind.REFRESH_FAILED, EventKind.REFRESH_CANCELLED):
                span.set_status(Status(StatusCode.ERROR))

        for span in self._resource_spans.pop(refresh_id, {}).values():
            _end(span, interrupt, end_time, failures)
        span = self._refresh_spans.pop(refresh_id, None)
        if span is not None:
            _end(span, complete, end_time, failures)
        if failures:
            raise failures[0]

    def _remember(self, refresh_id: str, sequence: int) -> bool:
        """Record a refresh finished by an event without lifecycle identity.

        Returns whether the event is the first known end of the refresh.
        """
        finished = self._finished
        if refresh_id in finished or (
            refresh_id not in self._refresh_spans and sequence <= self._forgotten
        ):
            return False
        finished[refresh_id] = sequence
        if len(finished) > _FINISHED_MEMORY:
            _, forgotten = finished.popitem(last=False)
            self._forgotten = max(self._forgotten, forgotten)
        return True


_FINISHED_MEMORY: Final = 1024
_REFRESH_ATTRIBUTES: Final = (
    "status",
    "mode",
    "trigger",
    "duration",
    "refreshed",
    "deleted",
    "failed",
)
_RESOURCE_TERMINAL: Final = frozenset(
    {
        EventKind.RESOURCE_REFRESH_COMPLETED,
        EventKind.RESOURCE_REFRESH_FAILED,
        EventKind.RESOURCE_REFRESH_SKIPPED,
    }
)
_INTERRUPTED_STATUS: Final[Mapping[EventKind, Status]] = MappingProxyType(
    {
        EventKind.REFRESH_COMPLETED: Status(
            StatusCode.ERROR, "the refresh finished before the operation reported"
        ),
        EventKind.REFRESH_FAILED: Status(
            StatusCode.ERROR, "the refresh failed before the operation finished"
        ),
        EventKind.REFRESH_CANCELLED: Status(
            StatusCode.ERROR, "the refresh was cancelled before the operation finished"
        ),
        EventKind.REFRESH_SKIPPED: Status(
            StatusCode.ERROR, "the refresh was skipped before the operation finished"
        ),
    }
)
_REFRESH_TERMINAL: Final = frozenset(_INTERRUPTED_STATUS)


def _end(
    span: Span,
    prepare: Callable[[Span], None],
    end_time: int,
    failures: list[Exception],
) -> None:
    """End `span` after `prepare`, recording rather than raising failures."""
    try:
        prepare(span)
    except Exception as error:
        failures.append(error)
    try:
        span.end(end_time=end_time)
    except Exception as error:
        failures.append(error)


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
