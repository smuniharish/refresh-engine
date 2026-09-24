# Triggers and scheduling

All integrations normalize to a `RefreshRequest`.

- call `refresh()` or `submit()` for manual work;
- use `EventTrigger(mapper).request_for(event)` to map an event;
- use `QueryTrigger().request(ids)` for targeted query work;
- use `AsyncScheduler` in an existing event loop;
- use `ExternalSchedulerAdapter(engine.submit)` when another system owns timing.

```python
from refresh_engine import AsyncScheduler, TriggerSource

scheduler = AsyncScheduler(
    lambda: engine.refresh(trigger=TriggerSource.SCHEDULED),
    interval=60,
    run_immediately=True,
)
await scheduler.start()
await shutdown_requested.wait()
await scheduler.stop()
await engine.close()
```

`start()` returns after installing the schedule. With `run_immediately=True`, the
callback runs once without waiting for the first interval. The scheduler example then
invokes the external adapter and prints:

```text
external refresh: status=success
```

Configure `OverlapPolicy` on `RefreshConfig`: skip, queue, coalesce, or cancel the
previous request. Cancellation remains cooperative; operations must not suppress
`CancelledError`.
