# Performance

refresh-engine is designed so that the cost of a refresh follows the amount of
change, not the size of the population.

## Benchmark results

The benchmark in the repository measures the engine's own overhead with in-memory
sources and operations. On CPython 3.12 and Windows 11:

| Resources | Unchanged | 1,000 changed | Full refresh | Peak memory (full) |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.003 s | 0.016 s | 0.016 s | 1.1 MB |
| 10,000 | 0.027 s | 0.069 s | 0.315 s | 10.5 MB |
| 100,000 | 0.555 s | 0.488 s | 3.141 s | 114.2 MB |

Compared with the previous release, a full refresh of 100,000 resources is three
times faster and needs less than a quarter of the memory. The
[benchmark README](https://github.com/smuniharish/refresh-engine/blob/HEAD/benchmarks/README.md)
describes the workload and the full results.

## How the engine stays fast

- **Cheap indicators skip reads.** A resource whose reliable indicator is unchanged
  is neither read nor hashed.
- **Only changed state is written.** Unchanged resources cost a comparison, not a
  database write, and an unchanged population commits nothing.
- **Snapshots are released early.** Only snapshots of resources that will receive an
  operation stay in memory.
- **Bounded concurrency without overhead.** Snapshot reads and operations share a
  fixed pool of workers rather than one task per resource.
- **Events cost nothing without subscribers.** The engine does not build events
  that nobody receives.

## Tuning

- **Provide reliable cheap indicators.** They are the single largest saving for
  large, mostly unchanged populations.
- **Size `max_concurrency` to your I/O.** It bounds concurrent snapshot reads and
  concurrent operations; raise it for high-latency remote calls and lower it to
  protect rate-limited systems.
- **Fingerprint only what matters.** A narrow strategy hashes less data and avoids
  refreshes caused by irrelevant changes.
- **Keep event handlers fast.** Synchronous handlers run on the event loop; move
  slow work into coroutine handlers or offload it to a thread.
- **Prefer `INCREMENTAL` and targeted refreshes.** Reserve `FULL` refreshes for
  rebuilds.

Discovery and change detection are linear in the number of resources, and planning
is linear in resources plus dependencies.
