# Concurrency, overlap, retries, and timeouts

Source: `src/refresh_engine/core/models.py` (`RefreshConfig`, `RetryConfig`,
`TimeoutConfig`), `src/refresh_engine/execution/coordinator.py`,
`src/refresh_engine/execution/executor.py`,
`examples/08_custom_store_and_overlap.py` (verified output below).

## `RefreshConfig`

```python
from refresh_engine import RefreshConfig, OverlapPolicy, FailurePolicy, RetryConfig, TimeoutConfig

config = RefreshConfig(
    max_concurrency=10,                      # bounded concurrent resource operations
    overlap_policy=OverlapPolicy.COALESCE,    # default
    failure_policy=FailurePolicy.BEST_EFFORT, # default
    retry=RetryConfig(),                       # max_attempts=1 by default (no retry)
    timeout=TimeoutConfig(),                   # all None by default (no timeouts)
    cancel_active_on_shutdown=False,
)
```

`max_concurrency` must be `>= 1`; timeouts must be `> 0` if set — both raise
`ValueError` in `__post_init__` otherwise.

## Bounded concurrency

`RefreshExecutor.execute()` creates an `asyncio.Semaphore(max_concurrency)` and
runs each dependency level's items concurrently through it; snapshot/fingerprint
work during discovery is similarly worker-pooled at
`min(max_concurrency, len(resources))`. **Never bypass this by spawning your
own unbounded tasks around the engine** — if you need higher throughput,
increase `max_concurrency` deliberately and size it to your downstream
system's real capacity (connection pools, rate limits), not "as high as
possible."

## Overlap policy (`OverlapPolicy`, on `RefreshCoordinator`)

| Policy | Behavior when a refresh is requested while one is already running/queued |
| --- | --- |
| `SKIP_IF_RUNNING` | Returns `RefreshResult.skipped("refresh already running")` immediately; the new request does not run at all. |
| `QUEUE` | The new request is queued and runs after the current one finishes, in submission order. |
| `COALESCE` (default) | The new request is merged (`RefreshRequest.merge`) into the already-pending request; both callers' futures resolve to the one merged run's result. |
| `CANCEL_PREVIOUS` | The active run (and any other pending ones) is cancelled; the new request becomes the one that runs. |

`RefreshRequest.merge` widens `mode` to `FULL` if either side is `FULL`, else
to `DEPENDENCY_AWARE` if either side is `DEPENDENCY_AWARE`, unions
`resource_ids`, keeps the max `priority`, merges `metadata`, keeps the earliest
`requested_at`, and prefers the newer `correlation_id`.

### Verified example output

`examples/08_custom_store_and_overlap.py` — two concurrent `engine.refresh()`
calls under the default `COALESCE` policy:

```text
coalesced requests: first=success, second=success, store_loads=1
```

Both callers get a successful result from the single merged run; the store's
`load_all()` is invoked once, not twice.

## Failure policy (`FailurePolicy`)

| Policy | Behavior |
| --- | --- |
| `FAIL_FAST` | Stop executing further plan levels as soon as any resource in a level fails; do not persist any state from that refresh if any resource failed. |
| `BEST_EFFORT` (default) | Keep executing remaining resources/levels; report `RefreshStatus.PARTIAL` if some failed, `SUCCESS` if none did. |
| `RETRY_THEN_CONTINUE` | Retry failed resources per `RetryConfig`, then continue with best-effort semantics for whatever still fails. |

## `RetryConfig`

```python
RetryConfig(
    max_attempts=1,             # 1 = no retry; must be >= 1
    initial_delay=0.1,
    max_delay=5.0,
    exponential_base=2.0,
    jitter=0.0,
    retryable_exceptions=(Exception,),
)
```

Retries are implemented with **Tenacity** (`AsyncRetrying`,
`stop_after_attempt`, `wait_exponential` + `wait_random` jitter) inside
`RefreshExecutor._execute_item`, scoped to a single resource's operation call —
not to discovery or the whole refresh.

## `TimeoutConfig`

```python
TimeoutConfig(
    discovery=None,         # wraps ResourceSource.discover() consumption
    snapshot=None,          # wraps each ResourceSource.snapshot() call
    resource_refresh=None,  # wraps each RefreshOperation call
    overall=None,           # wraps the entire refresh lifecycle
)
```

All default to `None` (no timeout). Any configured timeout uses
`asyncio.wait_for` and raises the equivalent `TimeoutError`/`CancelledError`
path — treat exceeded timeouts the same as any other failure for that
resource (see [failure-handling.md](failure-handling.md)); the corresponding
operation may be retried per `RetryConfig`, so **operations must be
idempotent**.

## Verified retry example

`examples/06_resilience_and_partial_discovery.py` with
`FailurePolicy.RETRY_THEN_CONTINUE` and `RetryConfig(max_attempts=2, initial_delay=0)`,
where the `alpha` resource fails once then succeeds:

```text
retry: status=success, attempts={'alpha': 2, 'beta': 1}
```

## Common mistakes

- Setting `max_concurrency` very high "for speed" without considering the
  downstream system's real capacity.
- Relying on `FAIL_FAST` in a large batch when a single unrelated resource
  failure should not block hundreds of unrelated resources — prefer
  `BEST_EFFORT` or `RETRY_THEN_CONTINUE` for large heterogeneous resource
  sets.
- Configuring retries with a non-idempotent operation — a partially applied
  side effect can then be applied twice.
- Assuming `COALESCE` guarantees the merged request preserves your exact
  original `resource_ids`/`mode` — it widens per `RefreshRequest.merge`
  semantics above.
