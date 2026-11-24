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
- without subscribers, the engine does not even build events;
- unsubscribing stops delivery of new events, but a handler that received the start
  of a refresh or resource still receives the event that closes it, even if it
  unsubscribed in between, so `has_subscribers` stays `True` until those
  lifecycles close.

### Capacity and overload

The queue of undelivered events is bounded, so a slow handler can neither block a
refresh nor grow memory without limit:

```python
events = AsyncEventPublisher(capacity=100_000)  # the default
```

- While `capacity` events are waiting, newly published events are dropped; the
  oldest queued events are kept, so delivered events stay in publication order.
- Refresh boundary events (`REFRESH_STARTED`, the four terminal `REFRESH_*` events,
  and scheduler events) may use up to `AsyncEventPublisher.BOUNDARY_RESERVE` (1024)
  further slots, so they survive a burst of resource events.
- `events.dropped` counts dropped events, `events.queued` and `events.peak_queued`
  report the backlog, and the first drop of each overload episode is logged as a
  warning to the `refresh_engine` logger.
- While the queue is at least half full, the engine yields to the event loop after
  each operation, so handlers that keep up are not starved by operations that never
  suspend.

- Lifecycles are never left half-delivered. Once the publisher has queued a
  `REFRESH_STARTED` or `RESOURCE_REFRESH_STARTED` event, it always queues the event
  that closes it (the refresh's terminal event, or the resource's completed, failed,
  or skipped event), even past the reserve. Handlers that pair start and end
  events, such as tracing handlers, therefore never miss the end of a lifecycle
  whose start they received.
- The publisher tracks at most `capacity` open lifecycles (refreshes plus
  resources), so the queue never holds more than `2 * capacity + BOUNDARY_RESERVE`
  events. A tracked lifecycle is never evicted: while `capacity` lifecycles are
  open, the start of a new refresh, and of a new resource within a tracked refresh,
  is not delivered, so every delivered start keeps its closing event. With the
  default capacity this needs 100,000 concurrently open operations.
- `events.open_lifecycles` reports the tracked lifecycles, and
  `events.rejected_lifecycles` counts the starts that were not delivered because
  tracking was full; they are also counted in `dropped`, and the first of each
  episode is logged as a warning. The remaining events of a refresh whose start was
  not delivered are delivered on a best-effort basis.
- Repeated start events of an open lifecycle are delivered without using capacity.
  A lifecycle is released by its closing event, which is delivered to the handlers
  that received its start even after they unsubscribed, so capacity is never held
  by refreshes that finished. A `REFRESH_STARTED` that your own code publishes and
  never closes keeps its slot.

Delivery is therefore best-effort under sustained overload: a handler that is slower
than the refresh loses resource events, and if the reserve is exhausted as well,
boundary events that do not close an open lifecycle, such as a `REFRESH_STARTED`
whose refresh is then not delivered at all. Size `capacity` for the largest
refresh, keep handlers fast, and alert on `dropped`. `drain()` delivers whatever is
queued; events that were dropped are not delivered later.

### Interrupted delivery

Events are delivered by a background task. If that task is cancelled while a
handler is running, for example because the event loop is shutting down, the
publisher keeps the event: the next delivery task, started by `drain()` or the next
`publish()`, calls the interrupted handler again with the same event and then calls
the handlers after it, before delivering the rest of the queue. Handlers that had
already returned are not called again, and a handler unsubscribed in the meantime is
skipped unless the event closes a lifecycle whose start it received.

The interrupted handler may therefore see an event twice, and side effects it
performed before the interruption can be repeated; make such handlers idempotent.
Delivery is never exactly-once. A handler that raises `asyncio.CancelledError` while
the delivery task is not being cancelled has failed: the failure is logged and
delivery continues. `events.queued` counts events that have not been taken for
delivery yet; an interrupted event is not counted while it waits.

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
  event attributes. Failures carry an `exception` field with the redacted
  traceback; see [Exceptions](#exceptions).
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

### Exceptions

Exception messages often contain secrets, such as a connection URL with a password
or an HTTP error that echoes a header. Redacting fields does not reach them, so the
handler renders the exception itself:

- By default, [`redact_exception`][refresh_engine.redact_exception] formats the full
  traceback, including chained exceptions, exception groups, and notes, and passes
  it through [`redact_text`][refresh_engine.redact_text]. The exception type, the
  stack, and every non-sensitive part of the message are kept.
- `redact_text` removes values of credential-like keys written as `key=value`,
  `key: value`, or quoted JSON and Python mappings, credentials of `Bearer`,
  `Basic`, `Digest`, and `Token` authorization values, and the user information of
  URLs such as `postgresql://user:password@host`.
- Pass `exception_redactor=` to render exceptions your own way. If it raises or
  returns something other than a string, the handler logs only the exception type
  and a note that the details were withheld, never the original text.
- `raw_exceptions=True` attaches the unredacted exception as `exc_info` instead, so
  your structlog configuration renders it. It bypasses every redactor; enable it
  only where tracebacks are known to be safe.

Diagnostics that the package logs to the standard-library `refresh_engine` logger,
such as handler, callback, and rollback failures, include tracebacks redacted the
same way.

Redaction is pattern-based and therefore best-effort. It cannot recognize a secret
that appears without a recognizable key, scheme, or URL, for example a bare token
in free text, and local variable values are never rendered. Avoid putting secrets
into exception messages, and extend the redactor when your clients format them in
other ways.

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
messages are never recorded.

When a refresh ends while resource operations are still open, for example after a
cancellation or an overall timeout, its terminal event ends those spans with an
error status and the attribute `refresh_engine.resource.interrupted`. Resource
spans are only started while their refresh span is open. Because the publisher
always delivers the event that closes a lifecycle whose start it delivered, every
span is ended exactly once, even when the queue overflows. A failing metric
instrument, or a span operation that raises, never prevents the other spans from
ending.

Duplicate, late, and out-of-order events never start or end a span twice. Every
event the engine publishes carries an identity shared by all events of its
refresh, and the handler remembers each finished refresh by that identity for as
long as any of its events still exists. A replayed event of a finished refresh
therefore never reopens it, while a delayed `REFRESH_STARTED` of a refresh that has
not finished is traced, however many other refreshes finished in the meantime and
whichever publisher delivers it. No global order between publishers is assumed,
and this memory needs no limit: an identity is forgotten once no event can be
delivered with it any more.

Events that your own code creates have no such identity. For those, the handler
remembers the last 1,024 finished refresh IDs and recognizes older refreshes by
creation order: a `REFRESH_STARTED` event created before the terminal event of a
refresh the handler has since forgotten is treated as late and ignored. Such a
start that is delayed until more than 1,024 other refreshes have finished is
therefore not traced. Memory stays bounded by the open spans, those 1,024 IDs, and
the identities of events that still exist.

If you implement your own sink, both methods must be asynchronous; the API reference
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
