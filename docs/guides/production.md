# Production checklist

Review these points before running refresh-engine in production.

## Correctness

- [ ] Resource IDs are stable and unique across refreshes.
- [ ] `discover()` raises when the source is unavailable, and reports
      `complete=False` whenever the listing may be partial.
- [ ] Operations are idempotent and do not suppress `asyncio.CancelledError`.
- [ ] The fingerprint strategy covers every value that affects the operation's
      output, and nothing that changes on its own.
- [ ] Cheap indicators are marked reliable only when their equality proves the
      fingerprinted values are unchanged.
- [ ] Declared dependencies are complete, and cycles are fixed at the source.
- [ ] Sources, operations, and state stores never await a refresh of their own
      engine; follow-up refreshes are started in separate tasks.

## Durability

- [ ] A durable [state store](state-stores.md) keeps state across restarts, applies
      transactions atomically, and round-trips every field.
- [ ] Only one engine refreshes a given state namespace at a time.

## Resilience

- [ ] [Timeouts](failure-handling.md#timeouts) are set for discovery, snapshots,
      operations, and the whole refresh.
- [ ] [Retries](failure-handling.md#retries) cover transient errors only.
- [ ] The [failure policy](failure-handling.md#failure-policies) matches the
      destination: `BEST_EFFORT` for independent resources, `FAIL_FAST` for
      all-or-nothing updates.
- [ ] `max_concurrency` respects the rate limits of the source and the destination.
- [ ] The [overlap policy](scheduling.md#overlapping-requests) matches how requests
      arrive.

## Operations

- [ ] Schedulers and engines are used with `async with`, or stopped and closed on
      shutdown, scheduler first.
- [ ] Structured logs are enabled with a redactor that covers your metadata.
- [ ] Metrics track refresh statuses and durations, and alerts fire on repeated
      `FAILED` or `PARTIAL` refreshes.
- [ ] `PARTIAL` results are reviewed: their issues name the resources, phases, and
      exceptions involved.
