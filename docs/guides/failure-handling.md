# Failure handling

refresh-engine never lets one bad resource break a refresh, and never lets a failure
advance stored state. Failures are reported in the result rather than raised.

## Where failures appear

A [`RefreshResult`][refresh_engine.RefreshResult] reports failures in three places:

- `errors` holds one [`RefreshIssue`][refresh_engine.RefreshIssue] per failed
  resource, plus one for a failure of the refresh as a whole. Each issue names its
  [`RefreshPhase`][refresh_engine.RefreshPhase], the affected `resource_id` (or
  `None`), a message, and the exception type.
- `executions` holds the outcome of every planned operation, including attempts and
  duration.
- `warnings` holds non-fatal diagnostics, such as suppressed deletions.

| Phase | Typical cause | Scope |
| --- | --- | --- |
| `persistence` | The state store failed to load or commit. | Refresh |
| `discovery` | `discover()` raised, timed out, or returned invalid data. | Refresh |
| `snapshot` | `snapshot()` raised, timed out, or returned an invalid snapshot. | Resource |
| `fingerprint` | The fingerprint strategy could not fingerprint the snapshot. | Resource |
| `planning` | A dependency cycle, or a selector that raised. | Resource or refresh |
| `execution` | The operation raised or timed out, or a dependency failed. | Resource |

Lifecycle events carry the original exception, so logs keep the full traceback; see
[Events, logging, and metrics](observability.md).

## Statuses

| Status | Meaning |
| --- | --- |
| `SUCCESS` | No issue was recorded. |
| `PARTIAL` | Some resources failed, but at least one in-scope resource was handled successfully. |
| `FAILED` | A refresh-level failure occurred, the fail-fast policy stopped the refresh, or every in-scope resource failed. |
| `CANCELLED` | The refresh was superseded or cancelled by an engine shutdown. |
| `SKIPPED` | The request did not run because another refresh was running. |

## Retries

Transient failures are retried with exponential backoff and jitter:

```python
from refresh_engine import RefreshConfig, RetryConfig

config = RefreshConfig(
    retry=RetryConfig(
        max_attempts=4,
        initial_delay=0.5,
        max_delay=10,
        exponential_base=2,
        jitter=0.5,
        retryable_exceptions=(ConnectionError, TimeoutError),
    )
)
```

The delay before attempt *n + 1* is `initial_delay * exponential_base ** (n - 1)`
plus up to `jitter` seconds, capped at `max_delay`. Exceptions that are not listed
in `retryable_exceptions` fail immediately. Retries apply to operations; a failed
snapshot is retried by the next refresh.

## Timeouts

[`TimeoutConfig`][refresh_engine.TimeoutConfig] bounds each phase in seconds:

| Timeout | Bounds |
| --- | --- |
| `discovery` | Starting and fully consuming a discovery. |
| `snapshot` | Each snapshot read. |
| `operation` | Each operation attempt. |
| `overall` | Discovery through execution of one refresh. |

When the overall timeout expires, unfinished work is cancelled and the refresh
fails. Under the best-effort policy, the state of every operation that had already
succeeded is still committed, so long refreshes make progress across runs.
Persisting state is not bounded by the overall timeout.

## Failure policies

| Policy | Behavior |
| --- | --- |
| `BEST_EFFORT` (default) | Continue after failures and commit the state of every successful resource. |
| `FAIL_FAST` | Stop starting new work after the first failure, let running work finish, and commit nothing. |

```python
from refresh_engine import FailurePolicy, RefreshConfig

config = RefreshConfig(failure_policy=FailurePolicy.FAIL_FAST)
```

Use `FAIL_FAST` when a destination must change all at once or not at all.

## Discovery failures

A discovery that raises or times out fails the whole refresh before any change is
detected, so a broken source is never mistaken for an empty one. A discovery that
returns `complete=False` is used normally, except that no deletion is inferred; the
result lists how many deletions were suppressed.

## Automatic retries

Every failed resource is retried by a later refresh without any bookkeeping on your
side:

- a resource whose snapshot or operation failed keeps its previous state, so the
  next refresh detects the same change again;
- an unchanged resource whose planned refresh did not complete, for example a
  dependent skipped after a failure or an operation interrupted by the overall
  timeout, is stored with a retry marker that the next refresh treats as modified;
- a failed deletion is planned again by the next complete discovery.

## Example

```python
--8<--
examples/06_failure_handling.py
--8<--
```

```text
--8<--
examples/output/06_failure_handling.txt
--8<--
```
