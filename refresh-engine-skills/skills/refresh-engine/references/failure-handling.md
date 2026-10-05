# Failure handling

## Reading a result

`refresh()` returns a `RefreshResult` instead of raising for operational failures:

```python
from refresh_engine import RefreshStatus

result = await engine.refresh()
if result.status is not RefreshStatus.SUCCESS:
    for issue in result.errors:
        logger.warning(
            "refresh issue",
            phase=issue.phase,
            resource_id=issue.resource_id,
            error_type=issue.exception_type,
            message=issue.message,
        )
```

- `errors`: one `RefreshIssue` per failed resource, plus one for a refresh-level
  failure, which has `resource_id=None`.
- `executions`: one `ResourceExecutionResult` per planned operation, with
  `succeeded`, `attempts`, `duration`, and `error`.
- `warnings`: non-fatal notes, such as suppressed deletions, unknown targeted IDs,
  or source diagnostics.
- `failed_count` equals `len(errors)`.

| Phase | Cause | Scope |
| --- | --- | --- |
| `persistence` | The state store failed to load or commit. | Refresh |
| `discovery` | `discover()` raised, timed out, or returned invalid data. | Refresh |
| `snapshot` | `snapshot()` raised, timed out, or returned an invalid snapshot. | Resource |
| `fingerprint` | The strategy could not fingerprint the snapshot. | Resource |
| `planning` | A dependency cycle, or a selector that raised. | Resource or refresh |
| `execution` | The operation raised or timed out, or a dependency failed. | Resource |

| Status | Meaning |
| --- | --- |
| `SUCCESS` | No issue. |
| `PARTIAL` | Some resources failed, but at least one in-scope resource was handled. |
| `FAILED` | A refresh-level failure, a fail-fast stop, an overall timeout, or every in-scope resource failed. |
| `CANCELLED` | Superseded under `CANCEL_PREVIOUS`, or cancelled by `close(cancel=True)`. |
| `SKIPPED` | Not run under `SKIP_IF_RUNNING` because another refresh was active. |

## Retries

```python
from refresh_engine import RefreshConfig, RetryConfig

config = RefreshConfig(
    retry=RetryConfig(
        max_attempts=4,
        initial_delay=0.5,
        max_delay=10.0,
        exponential_base=2.0,
        jitter=0.5,
        retryable_exceptions=(ConnectionError, TimeoutError),
    )
)
```

- Retries apply to operations. The delay before attempt *n + 1* is
  `initial_delay * exponential_base ** (n - 1)` plus up to `jitter` seconds,
  capped at `max_delay`.
- Exceptions outside `retryable_exceptions` fail immediately. List only transient
  errors, so that bugs are not retried.
- `max_attempts=1`, the default, disables retries.
- A failed snapshot is not retried within the refresh; the next refresh reads it
  again.

## Timeouts

```python
from refresh_engine import RefreshConfig, TimeoutConfig

config = RefreshConfig(
    timeout=TimeoutConfig(discovery=60, snapshot=10, operation=30, overall=900)
)
```

| Timeout | Bounds |
| --- | --- |
| `discovery` | Starting and fully consuming the discovery. |
| `snapshot` | Each snapshot read. |
| `operation` | Each operation attempt; a timeout counts as a `TimeoutError` for retries. |
| `overall` | Discovery through execution. Persistence runs afterwards and is not bounded. |

When the overall timeout expires, unfinished work is cancelled and the refresh
fails. Under `BEST_EFFORT`, the operations that already succeeded are still
committed, so long refreshes make progress. Bound the state store with the database
driver's own timeouts. Always set timeouts in production, because the defaults are
unbounded.

## Failure policies

| Policy | Behavior |
| --- | --- |
| `FailurePolicy.BEST_EFFORT` (default) | Continue after failures, and commit every success. |
| `FailurePolicy.FAIL_FAST` | Stop starting new work after the first failure, let running work finish, and commit nothing. |

Use `FAIL_FAST` only when the destination must change all at once or not at all.

## Automatic retries across refreshes

No bookkeeping is needed:

- a failed snapshot or operation keeps the previous state, so the next refresh
  detects the change again;
- an unchanged resource whose planned refresh did not complete, such as a skipped
  dependent or an operation interrupted by the overall timeout, is stored with a
  retry marker, so the next refresh treats it as modified;
- a failed deletion is planned again by the next complete discovery.

## Discovery failures

A discovery that raises, times out, or returns invalid data fails the whole refresh
before change detection, so a broken source is never mistaken for an empty one. A
discovery with `complete=False` is used, but no deletion is inferred.
