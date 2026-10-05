"""Measure refresh-engine orchestration overhead on synthetic workloads.

Every sample builds a fresh engine, runs an initial refresh as a baseline, applies a
change profile, and measures the second refresh. Sources, snapshots, operations,
and state are in memory, so the numbers isolate the engine's own overhead.

Timing samples run without memory tracing. Peak memory is measured in a separate
pass with `tracemalloc`, which slows execution down.

Usage:
    python benchmarks/benchmark_refresh.py
    python benchmarks/benchmark_refresh.py --sizes 1000 --profiles unchanged full
    python benchmarks/benchmark_refresh.py --repeat 5 --json results.json
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import platform
import statistics
import tracemalloc
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from refresh_engine import (
    DiscoveryResult,
    EventKind,
    EventPublisher,
    PlanAction,
    RefreshEngine,
    RefreshEvent,
    RefreshMode,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
    __version__,
)

SIZES = (1_000, 10_000, 100_000)
PROFILES = ("unchanged", "one", "hundred", "thousand", "full")
CHANGES = {"one": 1, "hundred": 100, "thousand": 1_000}
PHASES = {
    EventKind.DISCOVERY_COMPLETED: "discovery_seconds",
    EventKind.SNAPSHOT_COMPLETED: "snapshot_seconds",
    EventKind.EXECUTION_COMPLETED: "execution_seconds",
    EventKind.STATE_PERSISTED: "persistence_seconds",
}


class Source:
    """Resources with a reliable cheap indicator equal to their content."""

    def __init__(self, size: int) -> None:
        self.values = [0] * size

    def _resources(self) -> Iterator[Resource]:
        for index, value in enumerate(self.values):
            yield Resource(
                f"resource-{index}",
                cheap_indicator=str(value),
                cheap_indicator_reliable=True,
            )

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(self._resources())

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        index = int(resource.resource_id.removeprefix("resource-"))
        return ResourceSnapshot(resource.resource_id, content=self.values[index])


async def operation(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    await asyncio.sleep(0)


def apply_profile(source: Source, profile: str) -> RefreshMode:
    for index in range(min(CHANGES.get(profile, 0), len(source.values))):
        source.values[index] += 1
    return RefreshMode.FULL if profile == "full" else RefreshMode.INCREMENTAL


async def sample(
    size: int, profile: str, *, memory: bool, phases: bool
) -> dict[str, Any]:
    source = Source(size)
    events = EventPublisher()
    durations: dict[str, float] = {}
    if phases:

        def record(event: RefreshEvent) -> None:
            name = PHASES.get(event.kind)
            if name is not None:
                durations[name] = float(event.attributes["duration"])

        events.subscribe(record)
    async with RefreshEngine(source, operation, events=events) as engine:
        await engine.refresh()
        await events.drain()
        durations.clear()
        mode = apply_profile(source, profile)
        gc.collect()
        if memory:
            tracemalloc.start()
        started = perf_counter()
        result = await engine.refresh(mode=mode)
        elapsed = perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1] if memory else None
        if memory:
            tracemalloc.stop()
        await events.drain()
    measurement: dict[str, Any] = {
        "seconds": elapsed,
        "status": str(result.status),
        "refreshed": result.refreshed_count,
        "unchanged": result.unchanged_count,
        **durations,
    }
    if peak is not None:
        measurement["peak_memory_bytes"] = peak
    return measurement


async def run(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    results = []
    for size in arguments.sizes:
        for profile in arguments.profiles:
            timings = [
                await sample(size, profile, memory=False, phases=arguments.phases)
                for _ in range(arguments.repeat)
            ]
            summary: dict[str, Any] = {
                "size": size,
                "profile": profile,
                "repeat": arguments.repeat,
                "seconds_median": statistics.median(
                    timing["seconds"] for timing in timings
                ),
                "seconds_samples": [timing["seconds"] for timing in timings],
                "status": timings[-1]["status"],
                "refreshed": timings[-1]["refreshed"],
                "unchanged": timings[-1]["unchanged"],
            }
            for name in PHASES.values():
                if name in timings[-1]:
                    summary[name] = statistics.median(
                        timing[name] for timing in timings
                    )
            if arguments.memory:
                traced = await sample(size, profile, memory=True, phases=False)
                summary["peak_memory_bytes"] = traced["peak_memory_bytes"]
            results.append(summary)
            print(json.dumps(summary, sort_keys=True), flush=True)
    return results


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark refresh-engine.")
    parser.add_argument("--sizes", nargs="+", type=int, default=list(SIZES))
    parser.add_argument(
        "--profiles", nargs="+", choices=PROFILES, default=list(PROFILES)
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--no-memory",
        dest="memory",
        action="store_false",
        help="skip the tracemalloc pass",
    )
    parser.add_argument(
        "--phases", action="store_true", help="record per-phase durations"
    )
    parser.add_argument("--json", type=Path, help="write a JSON report")
    arguments = parser.parse_args()
    if arguments.repeat < 1 or any(size < 1 for size in arguments.sizes):
        parser.error("sizes and --repeat must be positive")
    return arguments


def main() -> None:
    arguments = parse_arguments()
    results = asyncio.run(run(arguments))
    if arguments.json is not None:
        report = {
            "environment": {
                "refresh_engine": __version__,
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "date": datetime.now(UTC).date().isoformat(),
            },
            "results": results,
        }
        arguments.json.write_text(json.dumps(report, indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    main()
