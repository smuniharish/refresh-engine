"""Measure refresh-engine orchestration overhead on synthetic workloads.

Every sample builds a fresh engine, runs an initial refresh as a baseline, applies a
change profile, and measures the second refresh. Sources, snapshots, operations,
and state are in memory, so the numbers isolate the engine's own overhead.

Every snapshot carries a payload of `--payload-bytes` bytes, and operations run
with `--concurrency` in parallel.

Timing samples run without memory tracing. Peak memory is measured in a separate
pass with `tracemalloc`, which slows execution down.

Usage:
    python benchmarks/benchmark_refresh.py
    python benchmarks/benchmark_refresh.py --sizes 1000 --profiles unchanged full
    python benchmarks/benchmark_refresh.py --repeat 5 --phases --json results.json
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import gc
import json
import os
import platform
import statistics
import subprocess
import sys
import tracemalloc
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from refresh_engine import (
    AsyncEventPublisher,
    CompositeHash,
    DiscoveryResult,
    EventKind,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshEvent,
    RefreshMode,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
    ResourceSource,
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
PLANNING = "detection_and_planning_seconds"
FINGERPRINT_SAMPLE = 1_000


class Source(ResourceSource):
    """Resources with a reliable cheap indicator equal to their content."""

    def __init__(self, size: int, payload_bytes: int) -> None:
        self.values = [0] * size
        self.body = "x" * payload_bytes

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
        return ResourceSnapshot(
            resource.resource_id,
            content={"revision": self.values[index], "body": self.body},
            metadata={"kind": "document"},
        )


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
    size: int,
    profile: str,
    arguments: argparse.Namespace,
    *,
    memory: bool,
    phases: bool,
) -> dict[str, Any]:
    source = Source(size, arguments.payload_bytes)
    events = AsyncEventPublisher(capacity=max(100_000, 8 * size))
    durations: dict[str, float] = {}
    if phases:
        snapshotted: list[datetime] = []

        async def record(event: RefreshEvent) -> None:
            name = PHASES.get(event.kind)
            if name is not None:
                durations[name] = float(event.attributes["duration"])
            if event.kind is EventKind.SNAPSHOT_COMPLETED:
                snapshotted[:] = [event.timestamp]
            elif event.kind is EventKind.PLANNING_COMPLETED and snapshotted:
                durations[PLANNING] = (event.timestamp - snapshotted[0]).total_seconds()

        events.subscribe(record)
    config = RefreshConfig(max_concurrency=arguments.concurrency)
    async with RefreshEngine(source, operation, config=config, events=events) as engine:
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
    if events.dropped:
        raise RuntimeError(f"{events.dropped} events were dropped")
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


async def fingerprint_seconds_per_resource(payload_bytes: int) -> float:
    """Return the median time to fingerprint one snapshot of the benchmark's shape."""
    strategy = CompositeHash()
    body = "x" * payload_bytes
    snapshots = [
        ResourceSnapshot(
            f"resource-{index}",
            content={"revision": index, "body": body},
            metadata={"kind": "document"},
        )
        for index in range(FINGERPRINT_SAMPLE)
    ]
    timings = []
    for _ in range(5):
        started = perf_counter()
        for snapshot in snapshots:
            await strategy.fingerprint(snapshot)
        timings.append((perf_counter() - started) / FINGERPRINT_SAMPLE)
    return statistics.median(timings)


async def run(arguments: argparse.Namespace) -> tuple[float, list[dict[str, Any]]]:
    per_resource = await fingerprint_seconds_per_resource(arguments.payload_bytes)
    print(json.dumps({"fingerprint_seconds_per_resource": per_resource}), flush=True)
    results = []
    for size in arguments.sizes:
        for profile in arguments.profiles:
            timings = [
                await sample(
                    size, profile, arguments, memory=False, phases=arguments.phases
                )
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
            for name in (*PHASES.values(), PLANNING):
                if name in timings[-1]:
                    summary[name] = statistics.median(
                        timing[name] for timing in timings
                    )
            if arguments.memory:
                traced = await sample(
                    size, profile, arguments, memory=True, phases=False
                )
                summary["peak_memory_bytes"] = traced["peak_memory_bytes"]
            results.append(summary)
            print(json.dumps(summary, sort_keys=True), flush=True)
    return per_resource, results


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark refresh-engine.")
    parser.add_argument("--sizes", nargs="+", type=int, default=list(SIZES))
    parser.add_argument(
        "--profiles", nargs="+", choices=PROFILES, default=list(PROFILES)
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=1024,
        help="size of each snapshot's content body",
    )
    parser.add_argument(
        "--concurrency", type=int, default=10, help="RefreshConfig.max_concurrency"
    )
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
    if (
        arguments.repeat < 1
        or arguments.concurrency < 1
        or arguments.payload_bytes < 0
        or any(size < 1 for size in arguments.sizes)
    ):
        parser.error(
            "sizes, --repeat, and --concurrency must be positive, "
            "and --payload-bytes must not be negative"
        )
    return arguments


def git_revision() -> str | None:
    """Return the checked-out commit, suffixed `-dirty` when tracked files changed."""
    root = Path(__file__).resolve().parent.parent

    def git(*arguments: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", *arguments],  # noqa: S607
            capture_output=True,
            check=True,
            cwd=root,
            text=True,
        ).stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{commit}-dirty" if dirty else commit


def physical_memory_bytes() -> int | None:
    """Return the installed physical memory, if the platform reports it."""
    if sys.platform != "win32":
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (OSError, ValueError):
            return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = (
            ("length", ctypes.c_ulong),
            ("load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        )

    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return int(status.total_physical)
    return None


def main() -> None:
    arguments = parse_arguments()
    per_resource, results = asyncio.run(run(arguments))
    if arguments.json is not None:
        report = {
            "environment": {
                "refresh_engine": __version__,
                "commit": git_revision(),
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor() or None,
                "logical_cpus": os.cpu_count(),
                "physical_memory_bytes": physical_memory_bytes(),
                "date": datetime.now(UTC).date().isoformat(),
            },
            "options": {
                "repeat": arguments.repeat,
                "payload_bytes": arguments.payload_bytes,
                "concurrency": arguments.concurrency,
                "phases": arguments.phases,
                "memory": arguments.memory,
            },
            "fingerprint_seconds_per_resource": per_resource,
            "results": results,
        }
        arguments.json.write_text(json.dumps(report, indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    main()
