# Architecture overview

refresh-engine separates **what** is kept in sync, which is owned by the application,
from **how** it is kept in sync, which is owned by the engine.

![Architecture](../assets/diagrams/architecture-light.png#only-light)
![Architecture](../assets/diagrams/architecture-dark.png#only-dark)

## Responsibilities

The application provides:

- a **source** that discovers resources and reads their snapshots;
- an **operation** that applies refreshes and deletions to the destination;
- optionally, a **state store** for durable state, a **fingerprint strategy**, and
  **event handlers**.

The engine provides:

- **triggers**: direct calls, an interval scheduler, and a thread-safe bridge for
  external schedulers;
- **overlap coordination**, which runs one refresh at a time and applies the overlap
  policy to concurrent requests;
- the **refresh pipeline**, which observes resources, detects changes, plans
  operations, and executes them with bounded concurrency, retries, and timeouts;
- **state management**, which commits the state of successful resources atomically;
- **lifecycle events**, delivered to handlers off the refresh path.

## Design principles

**Domain-neutral core.** The engine knows nothing about what a resource is. Two
small contracts, the source and the operation, carry all domain knowledge, so the
same engine refreshes documents, configuration, files, plugin registries, or search
indexes.

**Safety before speed.** A failed or partial discovery never deletes anything. A
failed operation never advances stored state. A failing resource never blocks
unrelated resources, and a failing dependency skips only its dependents. Every
failed resource is retried by a later refresh.

**Work proportional to change.** Reliable cheap indicators skip reading unchanged
resources, fingerprints skip refreshing them, and only changed state is written. The
cost of a refresh follows the amount of change rather than the population size.

**Results, not exceptions.** Operational failures, such as an unavailable source, a
broken snapshot, or a rejected write, are reported in the result with their phase,
resource, and exception type. Exceptions are reserved for programming errors such as
invalid arguments.

**Explicit ownership of concurrency.** Every background task has an owner and a
shutdown path. Engines and schedulers are asynchronous context managers, cancellation
propagates cooperatively, and shutdown either waits for running work or cancels it
deliberately. Code that a refresh or a scheduled run waits for can close or stop
what it belongs to with `wait=False`, and a refresh awaited directly by the engine's
own source or operation fails with an error instead of deadlocking. Each refresh
runs in the context of the code that requested it.

**A small dependency footprint.** The engine relies on the standard library, plus
[tenacity](https://tenacity.readthedocs.io) for retries and
[structlog](https://www.structlog.org) for structured logging. Graph algorithms,
scheduling, and event delivery are implemented in the package and verified against
reference models.

## Guarantees

1. A discovery failure fails the refresh before any change is detected; it is never
   treated as an empty source.
2. Deletions are inferred only from a complete discovery.
3. State advances only for resources whose operation succeeded, and nothing advances
   under the fail-fast policy when anything failed.
4. Every refresh commits at most one state transaction.
5. Fingerprints are deterministic and type-sensitive.
6. Operations run after the operations of everything they depend on, and at most
   `max_concurrency` operations run at once.
7. Every refresh publishes exactly one terminal event, and handler failures never
   affect a refresh.
8. Every background task is owned and stopped deterministically.

These guarantees are verified by unit, integration, and property-based tests,
including a stateful test that compares the engine with an independent model across
thousands of generated scenarios.

## Complexity

For *V* discovered resources and *E* dependency edges, discovery, observation, and
change detection take *O(V)* time, and planning takes *O(V + E)*. Memory holds one
compact state per resource and only the snapshots of resources that are about to
receive an operation.

See [Lifecycle](lifecycle.md) for the phases of a single refresh.
