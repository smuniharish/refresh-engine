"""Shared domain-neutral fixtures for the runnable examples."""

from __future__ import annotations

from collections.abc import Mapping

from refresh_engine import DiscoveryResult, PlanAction, Resource, ResourceSnapshot


class MemorySource:
    def __init__(
        self,
        values: Mapping[str, object],
        *,
        dependencies: Mapping[str, set[str]] | None = None,
        complete: bool = True,
    ) -> None:
        self.values = dict(values)
        self.dependencies = dependencies or {}
        self.complete = complete

    async def discover(self) -> DiscoveryResult:
        async def stream():
            for resource_id in sorted(self.values):
                yield Resource(
                    resource_id,
                    dependencies=frozenset(self.dependencies.get(resource_id, set())),
                )

        return DiscoveryResult(stream(), complete=self.complete)

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource.resource_id,
            content=self.values[resource.resource_id],
            dependencies=resource.dependencies,
        )


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[PlanAction, str]] = []

    async def __call__(self, resource, snapshot, action, request) -> None:
        assert resource is not None
        self.calls.append((action, resource.resource_id))
