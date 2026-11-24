# Benchmarks

[`benchmark_refresh.py`](benchmark_refresh.py) measures the engine's own overhead.
Sources, snapshots, operations, and state are in memory, so the numbers exclude the
I/O that a real application performs.

Every sample builds a fresh engine, runs an initial refresh as a baseline, applies a
change profile, and times the second refresh:

| Profile | Change before the measured refresh | Mode |
| --- | --- | --- |
| `unchanged` | none | incremental |
| `one` | 1 resource | incremental |
| `hundred` | 100 resources | incremental |
| `thousand` | 1,000 resources | incremental |
| `full` | none | full (every resource is refreshed) |

Resources report a reliable cheap indicator, so unchanged resources are recognized
without reading a snapshot. Each snapshot carries a `--payload-bytes` body (1 KiB by
default) and metadata, and the engine runs with `--concurrency` operations at a time
(10 by default). Timings are medians of `--repeat` samples taken without memory
tracing; peak memory comes from a separate `tracemalloc` pass. A sample fails if the
event publisher drops an event, so the numbers always include full event delivery.

```console
python benchmarks/benchmark_refresh.py
python benchmarks/benchmark_refresh.py --sizes 10000 --profiles unchanged full
python benchmarks/benchmark_refresh.py --repeat 5 --phases --json results.json
python benchmarks/benchmark_refresh.py --payload-bytes 4096 --concurrency 50
```

`--phases` subscribes an event handler that records the duration of discovery,
snapshots, detection and planning, execution, and persistence. Every run also
reports the cost of fingerprinting one snapshot. `--json` writes the results with
the package version, Git commit, Python version, operating system, processor,
logical CPU count, physical memory, and the options used.

## Results

Measured with refresh-engine 1.1.0 (recorded in the raw report as its pre-release
working tree, `1.0.1` plus uncommitted changes), on
CPython 3.15.2, Windows 11 (10.0.26100), an Intel64 Family 6 Model 198 processor
with 20 logical CPUs, and 64 GiB of memory, using `--repeat 3 --phases` and the
default 1 KiB payload and concurrency of 10. The raw report is
[`results/windows-python-3.15-2026-11-24.json`](results/windows-python-3.15-2026-11-24.json).

| Resources | Profile | Time | Peak memory |
| ---: | --- | ---: | ---: |
| 1,000 | unchanged | 0.003 s | 0.4 MB |
| 1,000 | one | 0.003 s | 0.4 MB |
| 1,000 | hundred | 0.005 s | 0.5 MB |
| 1,000 | thousand | 0.025 s | 1.6 MB |
| 1,000 | full | 0.028 s | 1.6 MB |
| 10,000 | unchanged | 0.028 s | 3.7 MB |
| 10,000 | one | 0.033 s | 3.7 MB |
| 10,000 | hundred | 0.036 s | 3.8 MB |
| 10,000 | thousand | 0.065 s | 4.8 MB |
| 10,000 | full | 0.313 s | 15.6 MB |
| 100,000 | unchanged | 0.385 s | 39.2 MB |
| 100,000 | one | 0.519 s | 41.6 MB |
| 100,000 | hundred | 0.494 s | 41.7 MB |
| 100,000 | thousand | 0.518 s | 42.8 MB |
| 100,000 | full | 4.206 s | 164.6 MB |

For the 100,000-resource full refresh, the engine spends 0.15 s discovering, 1.90 s
reading and fingerprinting snapshots, 0.14 s detecting changes and planning, 1.23 s
running operations, and 0.09 s persisting state. Fingerprinting one snapshot costs
about 5.4 ?s.

### One million resources

A single-sample stress run (`--sizes 1000000 --profiles unchanged thousand full
--repeat 1 --phases`) on the same machine; the raw report is
[`results/windows-python-3.15-2026-11-24-1m.json`](results/windows-python-3.15-2026-11-24-1m.json).

| Profile | Time | Peak memory | Discovery | Snapshots | Detection and planning | Execution | Persistence |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| unchanged | 6.2 s | 369 MB | 1.8 s | 3.5 s | 0.3 s | | |
| thousand | 6.5 s | 391 MB | 1.5 s | 3.4 s | 0.3 s | 0.02 s | 0.4 s |
| full | 48.6 s | 1,589 MB | 1.8 s | 19.7 s | 4.6 s | 12.7 s | 1.8 s |

These are single measurements of in-memory overhead, not a tested operating
envelope. Real sources, operations, and state stores add their own I/O and memory.

Results vary with hardware and load. Compare runs only on the same machine, the
same Python version, and the same options, and record the environment with
`--json`.
