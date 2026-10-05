# Events, logging, and metrics

## Events

The engine publishes a `RefreshEvent` for every step. Each event has `kind` (an
`EventKind`), `refresh_id`, `resource_id`, `correlation_id`, a UTC `timestamp`,
kind-specific `attributes`, and, for failures, the exception as `error`.

| Kind | Attributes |
| --- | --- |
| `REFRESH_STARTED` | `mode`, `trigger`, and the request `metadata` when present |
| `DISCOVERY_STARTED`, `DISCOVERY_COMPLETED` | `discovered`, `complete`, `duration` |
| `SNAPSHOT_STARTED`, `SNAPSHOT_COMPLETED` | `resources`; then `snapshotted`, `cheap_matches`, `failed`, `duration` |
| `RESOURCE_SNAPSHOT_FAILED` | `phase`, `error_type` |
| `DETECTION_COMPLETED` | `added`, `modified`, `dependency_changed`, `unchanged`, `deleted` |
| `PLANNING_COMPLETED` | `refresh`, `delete`, `impacted` |
| `EXECUTION_STARTED`, `EXECUTION_COMPLETED` | `operations`; then `succeeded`, `failed`, `duration` |
| `RESOURCE_REFRESH_STARTED`, `RESOURCE_REFRESH_COMPLETED` | `action`; then `action`, `attempts`, `duration` |
| `RESOURCE_REFRESH_FAILED` | `action`, `attempts`, `duration`, `phase`, `error_type` |
| `RESOURCE_REFRESH_SKIPPED` | `action`, `reason` |
| `STATE_PERSISTED` | `written`, `deleted`, `duration` |
| `REFRESH_COMPLETED`, `REFRESH_FAILED`, `REFRESH_CANCELLED`, `REFRESH_SKIPPED` | `status`, `mode`, `trigger`, `duration`, and the result counts |
| `SCHEDULER_STARTED`, `SCHEDULER_STOPPED`, `SCHEDULER_RUN_FAILED` | `interval`; none; `error_type` |

Every refresh ends with exactly one terminal `REFRESH_*` event. Durations are in
seconds.

## Subscribing

```python
from refresh_engine import EventKind, EventPublisher, RefreshEngine, RefreshEvent

events = EventPublisher()


async def page_on_failure(event: RefreshEvent) -> None:
    if event.kind is EventKind.REFRESH_FAILED:
        await pager.notify(f"refresh {event.refresh_id} failed")


events.subscribe(page_on_failure)
engine = RefreshEngine(source, operation, events=events)
```

- Handlers are callables that take one event; they may be coroutine functions.
- Delivery happens in a background task, in publication order, so handlers never
  slow down or break a refresh. Synchronous handlers run on the event loop and must
  return quickly.
- Handlers run with a fresh context: context variables of the code that requested
  a refresh are not visible to them. Correlate with `refresh_id` and
  `correlation_id`.
- Handler exceptions are logged and kept in `events.errors`, and delivery
  continues.
- `await events.drain()` waits for pending deliveries; `engine.close()` drains
  automatically. Awaited directly by a handler, `drain()` returns immediately, so a
  handler may await `engine.close()`; through another task, use
  `engine.close(wait=False)`.
- Pass the same publisher to `AsyncScheduler(events=...)` to receive scheduler
  events too.

## Structured logging

```python
import structlog

from refresh_engine import EventPublisher, StructlogEventHandler

events = EventPublisher()
events.subscribe(StructlogEventHandler(structlog.get_logger("search_sync")))
```

- The log event name is the event kind, such as `refresh_completed`. Fields are the
  IDs, `occurred_at`, and the attributes. Failures carry the exception as
  `exc_info`.
- Levels: per-resource start and success at `debug`; resource failures, skipped
  operations, and cancellations at `warning`; refresh and scheduler failures at
  `error`; and everything else at `info`. Override them with
  `levels={EventKind.REFRESH_COMPLETED: "debug"}`.
- The handler does not configure structlog; the application owns processors and
  output.
- Fields pass through `default_redactor`, which masks credential-like keys such as
  `password`, `token`, `api_key`, `authorization`, `cookie`, and `dsn` in any
  naming style and at any depth. Pass `redactor=` to extend it. Still keep secrets
  out of request metadata.

## Metrics

`MetricsEventHandler(sink, prefix="refresh_engine")` turns events into metrics on
any object with `increment(name, value=1, attributes=None, /)` and
`observe(name, value, attributes=None, /)`:

| Metric | Kind | Attributes |
| --- | --- | --- |
| `refresh_engine.refreshes` | counter | `status`, `mode`, `trigger` |
| `refresh_engine.refresh.duration` | seconds | `status`, `mode` |
| `refresh_engine.resources.detected` | counter | `change` |
| `refresh_engine.operations` | counter | `action`, `outcome` |
| `refresh_engine.operation.duration` | seconds | `action`, `outcome` |
| `refresh_engine.snapshot.failures` | counter | `phase` |
| `refresh_engine.scheduler.failures` | counter | none |

An OpenTelemetry bridge:

```python
from opentelemetry import metrics

from refresh_engine import MetricsEventHandler, MetricsSinkABC

meter = metrics.get_meter("refresh_engine")


class OpenTelemetrySink(MetricsSinkABC):
    def __init__(self) -> None:
        self.counters = {}
        self.histograms = {}

    def increment(self, name, value=1, attributes=None, /) -> None:
        if name not in self.counters:
            self.counters[name] = meter.create_counter(name)
        self.counters[name].add(value, attributes=dict(attributes or {}))

    def observe(self, name, value, attributes=None, /) -> None:
        if name not in self.histograms:
            self.histograms[name] = meter.create_histogram(name, unit="s")
        self.histograms[name].record(value, attributes=dict(attributes or {}))


events.subscribe(MetricsEventHandler(OpenTelemetrySink()))
```

`InMemoryMetrics` is a thread-safe sink for tests; `snapshot()` returns the
recorded counters and observations.
