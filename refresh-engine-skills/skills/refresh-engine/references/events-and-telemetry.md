# Events and telemetry

Event-handler functions are async. For queued event publishing, the concrete
publisher is `AsyncEventPublisher`. Its queue is bounded by `capacity`; when
handlers fall behind, events are dropped and counted in `dropped`, so keep
handlers fast and monitor that counter. The event that closes a refresh or
resource is always delivered when its start event was. Open lifecycles are
tracked within `capacity`; when it is reached, new starts are not delivered
rather than evicting open ones, and `rejected_lifecycles` counts them. A
handler that unsubscribes still receives the closing events of lifecycles whose
start it received. If the
delivery task is cancelled while a handler runs, that handler receives the
event again later, so handlers with side effects should be idempotent.

`StructlogEventHandler` logs exceptions with credentials redacted by default.
Keep `raw_exceptions` disabled unless tracebacks are known to be safe to log.

Optional OpenTelemetry integration is installed with the `otel` extra:

```bash
pip install "refresh-engine[otel]"
```

The handler is `refresh_engine.otel.OpenTelemetryEventHandler`. See the
installed version's documentation for its constructor and event details; this
guide does not assume additional configuration or lifecycle behavior.

By default, spans omit resource and correlation IDs. Pass
`record_identifiers=True` only when those IDs are safe to export.
