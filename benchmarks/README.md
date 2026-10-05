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
without reading a snapshot. Timings are medians of `--repeat` samples taken without
memory tracing; peak memory comes from a separate `tracemalloc` pass.

```console
python benchmarks/benchmark_refresh.py
python benchmarks/benchmark_refresh.py --sizes 10000 --profiles unchanged full
python benchmarks/benchmark_refresh.py --repeat 5 --phases --json results.json
```

`--phases` subscribes an event handler that records the duration of discovery,
snapshots, execution, and persistence.

## Results

Measured with refresh-engine 0.2.0 on CPython 3.12.14, Windows 11, with
`--repeat 3 --phases` (the raw report is in
[`results/`](results/windows-python-3.12-2026-10-05.json)). The previous release,
0.1.0, was measured with the same workload and an equivalent event subscriber.

| Resources | Profile | 0.1.0 time | 0.2.0 time | Speed-up | 0.1.0 peak memory | 0.2.0 peak memory |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1,000 | unchanged | 0.006 s | 0.003 s | 2.0× | 1.0 MB | 0.3 MB |
| 1,000 | full | 0.070 s | 0.016 s | 4.4× | 4.9 MB | 1.1 MB |
| 10,000 | unchanged | 0.058 s | 0.027 s | 2.1× | 9.3 MB | 3.5 MB |
| 10,000 | thousand | 0.143 s | 0.069 s | 2.1× | 12.9 MB | 4.1 MB |
| 10,000 | full | 0.692 s | 0.315 s | 2.2× | 49.5 MB | 10.5 MB |
| 100,000 | unchanged | 1.075 s | 0.555 s | 1.9× | 104.4 MB | 37.4 MB |
| 100,000 | one | 0.776 s | 0.498 s | 1.6× | 104.4 MB | 39.6 MB |
| 100,000 | thousand | 1.053 s | 0.488 s | 2.2× | 107.6 MB | 40.4 MB |
| 100,000 | full | 9.499 s | 3.141 s | 3.0× | 513.4 MB | 114.2 MB |

For the 100,000-resource full refresh, 0.2.0 spends 0.19 s discovering, 1.21 s
reading and fingerprinting snapshots, 1.17 s running operations, and 0.08 s
persisting state.

Results vary with hardware and load. Compare runs only on the same machine, the
same Python version, and the same options, and record the environment with
`--json`.
