"""Reproducible orchestration benchmark; records no measurements in the repository."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import tracemalloc
from pathlib import Path
from time import perf_counter

from refresh_engine import (
    DiscoveryResult,
    EventKind,
    EventPublisher,
    RefreshEngine,
    RefreshMode,
    Resource,
    ResourceSnapshot,
)

SIZES = (1_000, 10_000, 100_000)
PROFILES = ("unchanged", "one", "hundred", "thousand", "full")


class BenchmarkSource:
    def __init__(self, size: int) -> None:
        self.values = [0] * size

    async def discover(self) -> DiscoveryResult:
        async def resources():
            for index, value in enumerate(self.values):
                yield Resource(
                    f"resource-{index}",
                    cheap_indicator=str(value),
                    cheap_indicator_reliable=True,
                )

        return DiscoveryResult(resources(), complete=True)

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        index = int(resource.resource_id.removeprefix("resource-"))
        return ResourceSnapshot(resource.resource_id, content=self.values[index])


def apply_profile(source: BenchmarkSource, profile: str) -> RefreshMode:
    change_counts = {"one": 1, "hundred": 100, "thousand": 1_000}
    if profile in change_counts:
        for index in range(min(change_counts[profile], len(source.values))):
            source.values[index] += 1
    return RefreshMode.FULL if profile == "full" else RefreshMode.INCREMENTAL


async def measure(size: int, profile: str) -> dict[str, object]:
    source = BenchmarkSource(size)
    active = 0
    max_active = 0
    events_seen = []
    events = EventPublisher()
    events.subscribe(events_seen.append)

    async def measured_no_op(resource, snapshot, action, request) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1

    engine = RefreshEngine(source, measured_no_op, events=events)
    await engine.refresh()
    await events.drain()
    events_seen.clear()
    max_active = 0
    mode = apply_profile(source, profile)

    tracemalloc.start()
    started = perf_counter()
    result = await engine.refresh(mode=mode)
    elapsed = perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    await events.drain()
    await engine.close()

    event_times = {event.kind: event.timestamp for event in events_seen}

    def phase_seconds(start: EventKind, end: EventKind) -> float:
        return (event_times[end] - event_times[start]).total_seconds()

    return {
        "size": size,
        "profile": profile,
        "seconds": elapsed,
        "discovery_seconds": phase_seconds(
            EventKind.DISCOVERY_STARTED, EventKind.DISCOVERY_COMPLETED
        ),
        "fingerprint_seconds": phase_seconds(
            EventKind.DISCOVERY_COMPLETED, EventKind.DETECTION_STARTED
        ),
        "detection_seconds": phase_seconds(
            EventKind.DETECTION_STARTED, EventKind.DETECTION_COMPLETED
        ),
        "refresh_seconds": phase_seconds(
            EventKind.EXECUTION_STARTED, EventKind.EXECUTION_COMPLETED
        ),
        "peak_memory_bytes": peak_bytes,
        "max_active_operations": max_active,
        "status": result.status.value,
        "discovered": result.discovered_count,
        "modified": result.modified_count,
        "unchanged": result.unchanged_count,
        "refreshed": result.refreshed_count,
        "failed": result.failed_count,
    }


async def run(args: argparse.Namespace) -> list[dict[str, object]]:
    results = []
    for size in args.sizes:
        for profile in args.profiles:
            samples = [await measure(size, profile) for _ in range(args.repeat)]
            durations = [float(sample["seconds"]) for sample in samples]
            peaks = [int(sample["peak_memory_bytes"]) for sample in samples]
            summary = dict(samples[-1])
            summary["repeat"] = args.repeat
            summary["seconds_samples"] = durations
            summary["seconds_median"] = statistics.median(durations)
            summary["peak_memory_bytes_max"] = max(peaks)
            results.append(summary)
            print(json.dumps(summary, sort_keys=True))
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=list(SIZES))
    parser.add_argument(
        "--profiles", nargs="+", choices=PROFILES, default=list(PROFILES)
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if args.repeat < 1 or any(size < 1 for size in args.sizes):
        parser.error("sizes and repeat must be positive")
    return args


def main() -> None:
    args = parse_args()
    results = asyncio.run(run(args))
    if args.json:
        report = {
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
            },
            "results": results,
        }
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
