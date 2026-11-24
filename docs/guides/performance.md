# Performance

refresh-engine is designed so that the cost of a refresh follows the amount of
change, not the size of the population.

## Benchmark results

The benchmark in the repository measures the engine's own overhead with in-memory
sources and operations, 1 KiB snapshot payloads, and a concurrency of 10. On
CPython 3.15 and Windows 11:

| Resources | Unchanged | 1,000 changed | Full refresh | Peak memory (full) |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.003 s | 0.025 s | 0.028 s | 1.6 MB |
| 10,000 | 0.028 s | 0.065 s | 0.313 s | 15.6 MB |
| 100,000 | 0.385 s | 0.518 s | 4.206 s | 164.6 MB |
| 1,000,000 | 6.2 s | 6.5 s | 48.6 s | 1,589 MB |

An incremental refresh of one million resources with a thousand changes takes about
as long as one with no changes, because unchanged resources are matched by their
cheap indicators. The
[benchmark README](https://github.com/smuniharish/refresh-engine/blob/HEAD/benchmarks/README.md)
describes the workload, the environment, and the per-phase results.

## How the engine stays fast

- **Cheap indicators skip reads.** A resource whose reliable indicator is unchanged
  is neither read nor hashed.
- **Only changed state is written.** Unchanged resources cost a comparison, not a
  database write, and an unchanged population commits nothing.
- **Bounded concurrency.** Snapshot reads and operations stay within the configured
  concurrency limit.

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
