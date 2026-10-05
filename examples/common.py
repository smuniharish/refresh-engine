"""In-memory source and operation shared by the examples."""

from __future__ import annotations

from collections.abc import Mapping

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


class DictionarySource:
    """Discover resources from a dictionary of contents.

    Attributes:
        contents: Resource content keyed by resource ID.
        dependencies: IDs each resource depends on.
        tags: Tags stored in each resource's metadata.
        complete: Whether discovery reports itself as complete.
    """

    def __init__(
        self,
        contents: Mapping[str, object],
        *,
        dependencies: Mapping[str, set[str]] | None = None,
        tags: Mapping[str, list[str]] | None = None,
    ) -> None:
        self.contents = dict(contents)
        self.dependencies = dict(dependencies or {})
        self.tags = dict(tags or {})
        self.complete = True

    async def discover(self) -> DiscoveryResult:
        resources = [
            Resource(
                resource_id,
                metadata={"tags": self.tags.get(resource_id, [])},
                dependencies=self.dependencies.get(resource_id, set()),
            )
            for resource_id in sorted(self.contents)
        ]
        return DiscoveryResult(resources, complete=self.complete)

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource.resource_id, content=self.contents[resource.resource_id]
        )


class Recorder:
    """Record every operation in the order it ran."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        self.calls.append(f"{action.value} {resource.resource_id}")

    def take(self) -> str:
        """Return the recorded calls as one line and clear them."""
        calls = ", ".join(self.calls) or "none"
        self.calls.clear()
        return calls
