# Events, stores, and scheduling

## Events

::: refresh_engine.EventPublisher

::: refresh_engine.RefreshEvent

::: refresh_engine.EventKind

## Observability

::: refresh_engine.StructlogEventHandler

::: refresh_engine.InMemoryMetrics

::: refresh_engine.default_redactor

## State

::: refresh_engine.InMemoryStateStore

## Scheduling

::: refresh_engine.AsyncScheduler

::: refresh_engine.ExternalSchedulerAdapter

The in-memory store does not survive process restart. Applications inject a persistent
store through the public protocol or ABC. The [store integration
example](../guides/examples-and-results.md#persistent-store-injection) demonstrates the
contract. `AsyncScheduler.stop()` is async.
