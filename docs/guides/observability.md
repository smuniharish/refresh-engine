# Events, logging, and metrics

## Lifecycle events

The engine and the scheduler publish a [`RefreshEvent`][refresh_engine.RefreshEvent]
for every step of their work. Each event carries its
[`EventKind`][refresh_engine.EventKind], the refresh ID, the affected resource, the
request's correlation ID, a UTC timestamp, kind-specific attributes, and, for
failures, the exception.

| Event | Attributes |
| --- | --- |
| `REFRESH_STARTED` | `mode`, `trigger`, and the request `metadata` when present |
| `DISCOVERY_STARTED` | — |
| `DISCOVERY_COMPLETED` | `discovered`, `complete`, `duration` |
| `SNAPSHOT_STARTED` | `resources` |
| `RESOURCE_SNAPSHOT_FAILED` | `phase`, `error_type` |
| `SNAPSHOT_COMPLETED` | `snapshotted`, `cheap_matches`, `failed`, `duration` |
| `DETECTION_COMPLETED` | `added`, `modified`, `dependency_changed`, `unchanged`, `deleted` |
| `PLANNING_COMPLETED` | `refresh`, `delete`, `impacted` |
| `EXECUTION_STARTED` | `operations` |
| `RESOURCE_REFRESH_STARTED` | `action` |
| `RESOURCE_REFRESH_COMPLETED` | `action`, `attempts`, `duration` |
| `RESOURCE_REFRESH_FAILED` | `action`, `attempts`, `duration`, `phase`, `error_type` |
| `RESOURCE_REFRESH_SKIPPED` | `action`, `reason` |
| `EXECUTION_COMPLETED` | `succeeded`, `failed`, `duration` |
| `STATE_PERSISTED` | `written`, `deleted`, `duration` |
| `REFRESH_COMPLETED`, `REFRESH_FAILED`, `REFRESH_CANCELLED`, `REFRESH_SKIPPED` | `status`, `mode`, `trigger`, `duration`, and the result counts |
| `SCHEDULER_STARTED` | `interval` |
| `SCHEDULER_STOPPED` | — |
| `SCHEDULER_RUN_FAILED` | `error_type` |

Every refresh ends with exactly one of the four terminal `REFRESH_*` events.
Durations are in seconds.

## Subscribing handlers

Pass an [`AsyncEventPublisher`][refresh_engine.AsyncEventPublisher] to the engine and subscribe
handlers to it. A handler must be an asynchronous function or an
[`EventHandler`][refresh_engine.EventHandler] subclass.

```python
from refresh_engine import EventKind, AsyncEventPublisher, RefreshEngine, RefreshEvent

events = AsyncEventPublisher()


async def alert_on_failure(event: RefreshEvent) -> None:
    if event.kind is EventKind.REFRESH_FAILED:
        await pager.notify(f"refresh {event.refresh_id} failed")


events.subscribe(alert_on_failure)
engine = RefreshEngine(source, operation, events=events)
```

The publisher delivers events from a background task, so handlers never slow down
or break a refresh:

- every handler receives every event in publication order, and handlers receive
  each event in subscription order;
- handlers are awaited before the next delivery;
- handlers run with a fresh context, so context variables set by the code that
  requested a refresh are not visible to them; correlate events with their
  `refresh_id` and `correlation_id` instead;
- a handler exception is logged to the `refresh_engine` logger and kept in
  `events.errors`, and delivery continues;
- `await events.drain()` waits until every published event has been delivered;
  `engine.close()` drains automatically. Awaited directly by a handler, `drain()`
  returns immediately, so a handler may await `engine.close()`. Awaiting them through
  another task, for example with `asyncio.gather`, would wait for the handler
  itself; use `engine.close(wait=False)` there;
- without subscribers, the engine does not even build events.

## Structured logging

[`StructlogEventHandler`][refresh_engine.StructlogEventHandler] logs every event
through [structlog](https://www.structlog.org):

```python
from refresh_engine import AsyncEventPublisher, StructlogEventHandler

events = AsyncEventPublisher()
events.subscribe(StructlogEventHandler())
```

- The event name is the event kind, for example `refresh_completed`.
- Fields are the refresh, resource, and correlation IDs, `occurred_at`, and the
  event attributes. Failures carry the exception as `exc_info`, so tracebacks
  render with your structlog configuration.
- Per-resource start and success events log at `debug`; resource failures, skipped
  operations, and cancellations at `warning`; refresh and scheduler failures at
  `error`; and everything else at `info`. Override levels with
  `levels={EventKind...: "info"}`.
- The handler does not configure structlog; your application owns processors and
  renderers.

Every field passes through a redactor first. The default,
[`default_redactor`][refresh_engine.default_redactor], replaces the values of
credential-like keys, such as `password`, `token`, `api_key`, `authorization`,
`cookie`, or `dsn`, in any naming style and at any nesting depth. Pass your own
`redactor` to extend it.

## Metrics

[`MetricsEventHandler`][refresh_engine.MetricsEventHandler] turns events into
counters and observations on any [`MetricsSink`][refresh_engine.MetricsSink]:

| Metric | Kind | Attributes |
| --- | --- | --- |
| `refresh_engine.refreshes` | counter | `status`, `mode`, `trigger` |
| `refresh_engine.refresh.duration` | observation, seconds | `status`, `mode` |
| `refresh_engine.resources.detected` | counter | `change` |
| `refresh_engine.operations` | counter | `action`, `outcome` |
| `refresh_engine.operation.duration` | observation, seconds | `action`, `outcome` |
| `refresh_engine.snapshot.failures` | counter | `phase` |
| `refresh_engine.scheduler.failures` | counter | — |

A custom sink subclasses [`MetricsSink`][refresh_engine.MetricsSink] and implements
asynchronous `increment()` and `observe()` methods. For example, the optional
OpenTelemetry integration provides an event handler that records metrics and spans:
import `OpenTelemetryEventHandler` from `refresh_engine.otel`.

```python
from opentelemetry import metrics, trace
from refresh_engine import AsyncEventPublisher

# OpenTelemetryEventHandler is provided by refresh_engine.otel.

events = AsyncEventPublisher()
events.subscribe(
    OpenTelemetryEventHandler(
        tracer=trace.get_tracer("refresh_engine"),
        meter=metrics.get_meter("refresh_engine"),
    )
)
engine = RefreshEngine(source, operation, events=events)
```

Install the optional dependencies with `pip install refresh-engine[otel]`. The
handler uses the supplied tracer and meter; your application configures and shuts
down the OpenTelemetry providers and exporters.

The [`OpenTelemetryEventHandler`][refresh_engine.otel.OpenTelemetryEventHandler]
creates one span per refresh and resource operation and reports the metrics above.
By default, spans carry only bounded attributes and the generated refresh ID. Pass
`record_identifiers=True` to also record resource and correlation IDs on spans;
enable it only when those IDs are safe to export. Refresh metadata and error
messages are never recorded. If you implement your own sink, both methods must be asynchronous; the API reference
documents their signatures.

[`InMemoryMetrics`][refresh_engine.InMemoryMetrics] is a thread-safe sink for tests
and small deployments.

## Example

```python
--8<--
examples/08_observability.py
--8<--
```

```text
--8<--
examples/output/08_observability.txt
--8<--
```
