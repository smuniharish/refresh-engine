# Exceptions

Failures that happen while a refresh runs are reported in
[`RefreshResult.errors`][refresh_engine.RefreshResult] rather than raised; the
`exception_type` of each issue names the class of the original exception.
Exceptions are raised directly for invalid arguments and for invalid lifecycle
calls.

::: refresh_engine.RefreshError

::: refresh_engine.ConfigurationError

::: refresh_engine.DiscoveryError

::: refresh_engine.SnapshotError

::: refresh_engine.FingerprintError

::: refresh_engine.DependencyCycleError

::: refresh_engine.StateStoreError

::: refresh_engine.SchedulerError

::: refresh_engine.EngineClosedError
