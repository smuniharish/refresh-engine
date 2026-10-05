# Extension contracts

Each extension point is available as a protocol, for structural typing, and as an
abstract base class, which enforces the required methods and, where useful, adds
reusable behavior. Protocol parameters are positional-only, so implementations may
name their parameters freely.

## Sources

::: refresh_engine.ResourceSource

::: refresh_engine.ResourceSourceABC

## Operations

::: refresh_engine.RefreshOperation

::: refresh_engine.RefreshOperationABC

## Fingerprint strategies

::: refresh_engine.FingerprintStrategy

::: refresh_engine.FingerprintStrategyABC

## State stores

::: refresh_engine.StateStore

::: refresh_engine.StateStoreABC

::: refresh_engine.StateTransaction

::: refresh_engine.StateTransactionABC

## Selectors

::: refresh_engine.ResourceSelector

::: refresh_engine.ResourceSelectorABC

## Metric sinks

::: refresh_engine.MetricsSink

::: refresh_engine.MetricsSinkABC
