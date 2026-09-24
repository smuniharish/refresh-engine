from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


async def stream(resources: list[Resource]) -> AsyncIterator[Resource]:
    for resource in resources:
        yield resource


@dataclass
class MutableSource:
    values: dict[str, Any]
    dependencies: dict[str, set[str]] = field(default_factory=dict)
    complete: bool = True
    fail_discovery: bool = False
    fail_snapshot: set[str] = field(default_factory=set)
    reliable_indicators: bool = True
    snapshot_calls: int = 0

    async def discover(self) -> DiscoveryResult:
        if self.fail_discovery:
            raise OSError("source unavailable")
        resources = [
            Resource(
                resource_id,
                version=str(value),
                dependencies=frozenset(self.dependencies.get(resource_id, set())),
                cheap_indicator=str(value),
                cheap_indicator_reliable=self.reliable_indicators,
            )
            for resource_id, value in self.values.items()
        ]
        return DiscoveryResult(stream(resources), complete=self.complete)

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        self.snapshot_calls += 1
        if resource.resource_id in self.fail_snapshot:
            raise OSError("snapshot unavailable")
        return ResourceSnapshot(
            resource.resource_id,
            content=self.values[resource.resource_id],
            version=resource.version,
            dependencies=resource.dependencies,
        )


@dataclass
class RecordingOperation:
    calls: list[tuple[str, PlanAction]] = field(default_factory=list)
    failures: set[str] = field(default_factory=set)

    async def __call__(
        self,
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        resource_id = (
            resource.resource_id
            if resource is not None
            else snapshot.resource_id if snapshot is not None else ""
        )
        self.calls.append((resource_id, action))
        if resource_id in self.failures:
            raise RuntimeError(f"failed {resource_id}")
