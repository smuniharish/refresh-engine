# Events and observability

Subscribe handlers to a shared `EventPublisher` before constructing the engine.
Handlers run in subscription order. Synchronous handlers run off the event loop;
handler errors are isolated and available through `EventPublisher.errors`.

```python
from refresh_engine import EventPublisher, RefreshEngine, StructlogEventHandler

events = EventPublisher()
events.subscribe(StructlogEventHandler())
engine = RefreshEngine(source, operation, events=events)
result = await engine.refresh()
await events.drain()
print(result.status.value, len(events.errors))
```

`structlog` is the package's logging dependency. The handler emits native
`refresh_event` records and deliberately does not configure global processors or a
renderer; the consuming application owns those choices. Event attributes may contain
application data. The handler redacts common secret names, but applications must provide a
stronger redactor for their schema.
Keep handlers fast and non-blocking. Use `refresh_id`, `resource_id`, and a caller
supplied `correlation_id` to correlate activity.

A configured handler emits one JSON object per lifecycle event. The example concludes
with:

```text
summary: status=success, event_kinds=13, handler_errors=0
```

`InMemoryMetrics` is a provider-neutral sink for adapters and tests; the engine does
not automatically emit into it. Event handlers can translate lifecycle events into
the deployment's metrics system.
