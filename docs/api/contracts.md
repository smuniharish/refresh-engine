# Extension contracts

## Protocols and abstract base classes

Every extension family has two integration styles:

- protocols preserve structural typing for existing objects that already have the
  required methods;
- ABCs provide inheritance, abstract-method enforcement, discoverable lifecycle
  methods, and reusable defaults.

Built-in stores, fingerprint strategies, selectors, event publishing, metrics, and
schedulers inherit the corresponding ABCs while still satisfying their protocols.
Applications can choose either style without adapters.

::: refresh_engine.ResourceSource

::: refresh_engine.FingerprintStrategy

::: refresh_engine.RefreshOperation

::: refresh_engine.StateStore

::: refresh_engine.StateTransaction

::: refresh_engine.ResourceSelector

::: refresh_engine.MetricsSink

## Abstract base classes

::: refresh_engine.ResourceSourceABC

::: refresh_engine.FingerprintStrategyABC

::: refresh_engine.RefreshOperationABC

::: refresh_engine.StateStoreABC

::: refresh_engine.StateTransactionABC

::: refresh_engine.ResourceSelectorABC

::: refresh_engine.EventPublisherABC

::: refresh_engine.MetricsSinkABC

::: refresh_engine.AsyncSchedulerABC
