# Lifecycle of a refresh

![The lifecycle of a refresh](../assets/diagrams/lifecycle-light.png#only-light)
![The lifecycle of a refresh](../assets/diagrams/lifecycle-dark.png#only-dark)

## Phases

| Phase | What happens | Events | On failure |
| --- | --- | --- | --- |
| Load state | The previous state of every resource is loaded from the state store. | — | The refresh fails. |
| Discovery | The source lists resources; duplicates and invalid items are rejected. | `DISCOVERY_STARTED`, `DISCOVERY_COMPLETED` | The refresh fails. |
| Observation | Each in-scope resource is matched by its cheap indicator, or read and fingerprinted, with bounded concurrency. | `SNAPSHOT_STARTED`, `RESOURCE_SNAPSHOT_FAILED`, `SNAPSHOT_COMPLETED` | The resource fails and keeps its previous state. |
| Detection | Observations are classified, and deletions are inferred from a complete discovery. | `DETECTION_COMPLETED` | — |
| Planning | The mode selects the resources to refresh and delete, dependencies order them, and cycles are isolated. | `PLANNING_COMPLETED` | Resources in a cycle fail; their dependents are skipped. |
| Execution | Operations run dependencies first, with bounded concurrency, retries, and timeouts. | `EXECUTION_STARTED`, `RESOURCE_REFRESH_*`, `EXECUTION_COMPLETED` | The resource fails; its dependents are skipped. |
| Persistence | Successful state, deletions, and retry markers are committed in one transaction. | `STATE_PERSISTED` | The refresh fails; nothing is committed. |

The refresh starts with `REFRESH_STARTED` and ends with exactly one of
`REFRESH_COMPLETED`, `REFRESH_FAILED`, `REFRESH_CANCELLED`, or `REFRESH_SKIPPED`.

## Timeouts

The overall timeout bounds discovery, observation, planning, and execution. When it
expires, unfinished work is cancelled and the refresh fails with an issue in the
phase that was running. Persistence then commits the work that had completed, unless
the fail-fast policy is in effect. Persistence itself is not bounded by the overall
timeout; bound it with your database driver's timeouts.

## Cancellation

Cancellation propagates through every phase: running snapshot reads and operations
are cancelled, an open transaction is rolled back, and the refresh ends with
`REFRESH_CANCELLED`. Its result reports the work done so far and the reason for the
cancellation.

## Overlap

An engine runs one refresh at a time. Requests that arrive during a refresh are
handled by the [overlap policy](../guides/scheduling.md#overlapping-requests): they
are merged, queued, skipped, or allowed to cancel the running refresh.
