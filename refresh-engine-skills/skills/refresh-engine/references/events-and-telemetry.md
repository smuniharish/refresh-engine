# Events and telemetry

Event-handler functions are async. For queued event publishing, the concrete
publisher is `AsyncEventPublisher`.

Optional OpenTelemetry integration is installed with the `otel` extra:

```bash
pip install "refresh-engine[otel]"
```

The handler is `refresh_engine.otel.OpenTelemetryEventHandler`. See the
installed version's documentation for its constructor and event details; this
guide does not assume additional configuration or lifecycle behavior.

By default, spans omit resource and correlation IDs. Pass
`record_identifiers=True` only when those IDs are safe to export.
