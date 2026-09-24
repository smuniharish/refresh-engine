# State and failure safety

`InMemoryStateStore` is the default and is useful for tests and process-local
workloads. Applications inject a persistent store through `StateStore` or
`StateStoreABC`. Using the source and operation from
[Getting started](../getting-started.md), a wrapper can add application behavior while
delegating transaction semantics to another store:

```python
from refresh_engine import InMemoryStateStore, RefreshEngine


class AuditedStore:
    def __init__(self) -> None:
        self.backend = InMemoryStateStore()
        self.load_count = 0

    async def load_all(self):
        self.load_count += 1
        return await self.backend.load_all()

    def transaction(self):
        return self.backend.transaction()

engine = RefreshEngine(
    source,
    operation,
    store=AuditedStore(),
)
```

The [persistent store example](examples-and-results.md#persistent-store-injection)
shows the public integration boundary. Database adapters are application-managed and
are not included in the distribution. Persistent adapters must commit staged changes
atomically and keep blocking database calls off the event loop.

The engine advances a resource's successful state only after its operation succeeds.
Discovery failure is not interpreted as an empty source. A
`DiscoveryResult(complete=False)` may contain useful resources, but omitted IDs are
retained and never planned as deletions.

`RefreshConfig` controls:

- `FailurePolicy.FAIL_FAST`, `BEST_EFFORT`, or `RETRY_THEN_CONTINUE`;
- a `RetryConfig` with attempts, backoff, jitter, and retryable exceptions;
- discovery, snapshot, resource-operation, and overall `TimeoutConfig` values;
- bounded `max_concurrency`.

Operations may be repeated after timeout or cancellation if the external side effect
completed but acknowledgement did not. Design them to be idempotent and use
application-level idempotency keys where available.

## Shutdown

Stop trigger intake before closing the engine. Stop an `AsyncScheduler` before closing
its engine:

```python
try:
    await scheduler.start()
    await shutdown_requested.wait()
finally:
    await scheduler.stop()
    await engine.close()
```

`cancel_active_on_stop` controls scheduler work, while
`RefreshConfig.cancel_active_on_shutdown` controls coordinated engine work. By
default, active work is allowed to finish. When cancellation is enabled, operations
must propagate `CancelledError` and remain safe to retry.

Cancellation before a successful operation does not commit state:

```text
cancellation: propagated=True, committed_states=0
```
