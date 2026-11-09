# Retries and telemetry

## Tenacity

The complete user-provided retry policy is passed as
`RetryConfig(retrying=AsyncRetrying(...))`:

```python
from tenacity import AsyncRetrying

from refresh_engine import RetryConfig

retry = RetryConfig(retrying=AsyncRetrying(...))
```

## OpenTelemetry

Install the optional extra with `pip install "refresh-engine[otel]"`. The
handler is `refresh_engine.otel.OpenTelemetryEventHandler`. Follow the installed
version's public signatures for construction and registration. Spans omit
resource and correlation IDs unless `record_identifiers=True` is passed.

## Events

Event-handler functions are async. `AsyncEventPublisher` is the queued
concrete publisher. For custom objects, subclass the matching ABC and inspect
the installed version's abstract methods. Do not infer queue lifecycle,
delivery guarantees, or telemetry configuration beyond these facts.
