# Models and configuration

## Resource and state

| Type | Purpose |
| --- | --- |
| `Resource(resource_id, metadata={}, version=None, dependencies=frozenset(), source=None, cheap_indicator=None, cheap_indicator_reliable=False)` | lightweight discovered identity |
| `ResourceSnapshot(resource_id, content=None, metadata={}, version=None, version_token=None, etag=None, dependencies=frozenset())` | observable fingerprint input |
| `DiscoveryResult(resources, complete=True, diagnostics=())` | async resource stream and deletion-safety signal |
| `ResourceFingerprint(algorithm, value)` | namespaced deterministic digest |
| `ResourceState` | last successfully processed fingerprint and snapshot metadata |

## Requests and results

`RefreshRequest` carries mode, selected IDs, trigger, priority, metadata, request
time, and correlation ID. `RefreshResult` carries status, timestamps, duration,
counts, warnings, errors, and per-resource executions.

`RefreshConfig(max_concurrency=10, overlap_policy=COALESCE,
failure_policy=BEST_EFFORT, retry=RetryConfig(), timeout=TimeoutConfig(),
cancel_active_on_shutdown=False)` configures execution.

`RetryConfig` configures attempts, exponential delay, jitter, and retryable exception
types. `TimeoutConfig` independently configures discovery, snapshot,
resource-operation, and overall seconds; `None` disables a timeout.

## Enumerations

`RefreshMode`, `TriggerSource`, `OverlapPolicy`, `FailurePolicy`, `RefreshStatus`,
`PlanAction`, and `ChangeType` are string enumerations exported by
`refresh_engine`.
