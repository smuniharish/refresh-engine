# Extension contracts

Extension points are abstract base classes that enforce the required methods and,
where useful, add reusable behavior. Subclass the corresponding base class when
implementing an extension.

## Sources

::: refresh_engine.ResourceSource

## Operations

An operation can be an asynchronous function or a subclass of
[`RefreshOperation`][refresh_engine.RefreshOperation]. The callable aliases
[`RefreshOperationCallable`][refresh_engine.RefreshOperationCallable] and
[`RefreshOperationLike`][refresh_engine.RefreshOperationLike] describe these forms.

::: refresh_engine.RefreshOperation

::: refresh_engine.RefreshOperationCallable

::: refresh_engine.RefreshOperationLike

## Fingerprint strategies

::: refresh_engine.FingerprintStrategy

## State stores

::: refresh_engine.StateStore

::: refresh_engine.StateTransaction

## Selectors

`ResourceSelector.matches()` is synchronous; selectors are used as pure predicates
while the engine plans a refresh.

::: refresh_engine.ResourceSelector

## Metric sinks

::: refresh_engine.MetricsSink
