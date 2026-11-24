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

Lifecycle events carry the original exception, and the structured-logging handler
logs its traceback with credentials redacted; see
[Events, logging, and metrics](observability.md).

Event handlers never affect a refresh. A handler that raises is logged and kept in
`events.errors`; a slow handler makes the bounded event queue drop events instead
of holding up the refresh, but the event that closes a refresh or resource is
always delivered when its start was. If the event-delivery task is cancelled while
a handler runs, that handler receives the event again later, so make handlers with
side effects idempotent; see
[Interrupted delivery](observability.md#interrupted-delivery).

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

To supply a custom tenacity policy, pass an `AsyncRetrying` instance. It takes
precedence over the individual `RetryConfig` fields:

```python
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt

from refresh_engine import RefreshConfig, RetryConfig

config = RefreshConfig(
    retry=RetryConfig(
        retrying=AsyncRetrying(
            stop=stop_after_attempt(4),
            retry=retry_if_exception_type((ConnectionError, TimeoutError)),
            reraise=True,
        )
    )
)
```

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

`FAIL_FAST` protects the engine's *state*, not the destination. After the first
failure:

- no further operation is started; the number of planned operations that never
  started is reported in `warnings`, and they have no entry in `executions`;
- operations that are already running finish, and their outcomes are recorded in
  `executions`;
- no state is committed, not even for operations that succeeded, and the result is
  `FAILED`.

Destination side effects of operations that succeeded before or during the failure
are **not** rolled back. Because their state was not committed, the next refresh
detects the same changes and runs those operations again. Operations must therefore
be idempotent. When a destination genuinely requires all-or-nothing updates, make it
take part in the transaction, for example by staging writes and publishing them in
a final step, or compensate in your own code; the engine does not undo work.

Use `FAIL_FAST` when later resources should not be processed once one has failed,
for example to stop early against a destination that is down.

## Asynchronous work and timeouts

Timeouts and cancellation in asyncio are cooperative. They take effect only when the
running coroutine reaches an `await` that suspends:

- an `async def` that performs blocking I/O or long computation without awaiting
  blocks the event loop; no timeout, retry delay, scheduler tick, or event delivery
  runs until it returns;
- a timeout cannot interrupt synchronous code that is already running, so a hung
  blocking call keeps its resources even after the refresh reports a timeout.

Run blocking I/O in a thread with `await asyncio.to_thread(...)`, and run heavy
CPU-bound work in a process pool through `loop.run_in_executor(...)`. A thread that
is abandoned by a timeout keeps running until its call returns, so blocking clients
still need their own timeouts. The built-in fingerprint strategies hash on the event
loop; for very large snapshots, fingerprint a compact projection of the content, or
write a strategy whose `fingerprint()` hashes in a thread with
`asyncio.to_thread(canonical_digest, value)`.

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
