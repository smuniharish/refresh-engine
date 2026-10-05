"""Test doubles shared by the test suite."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from refresh_engine import (
    DiscoveryResult,
    EventKind,
    PlanAction,
    RefreshEvent,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


async def stream(items: Iterable[Resource]) -> AsyncIterator[Resource]:
    """Yield `items` asynchronously, yielding to the event loop between items."""
    for item in items:
        await asyncio.sleep(0)
        yield item


@dataclass
class FakeSource:
    """A mutable, in-memory resource source.

    Attributes:
        values: Resource content keyed by ID; the discovery order is the key order.
        dependencies: Dependencies keyed by ID.
        dependencies_in_snapshot: Report dependencies from snapshots instead of
            discovery.
        cheap: Report each value as a reliable cheap indicator.
        complete: Value of `DiscoveryResult.complete`.
        tags: Tags stored in resource metadata, keyed by ID.
        discovery_error: Raised by `discover()` when set.
        snapshot_errors: Raised by `snapshot()` for the given IDs.
        snapshot_delay: Seconds each snapshot sleeps.
        asynchronous: Stream resources asynchronously instead of returning a list.
    """

    values: dict[str, Any]
    dependencies: dict[str, set[str]] = field(default_factory=dict)
    dependencies_in_snapshot: bool = False
    cheap: bool = False
    complete: bool = True
    tags: dict[str, list[str]] = field(default_factory=dict)
    discovery_error: Exception | None = None
    snapshot_errors: dict[str, Exception] = field(default_factory=dict)
    snapshot_delay: float = 0.0
    asynchronous: bool = True
    snapshot_calls: list[str] = field(default_factory=list)

    async def discover(self) -> DiscoveryResult:
        if self.discovery_error is not None:
            raise self.discovery_error
        resources = [self._resource(resource_id) for resource_id in self.values]
        items = stream(resources) if self.asynchronous else resources
        return DiscoveryResult(items, complete=self.complete)

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        resource_id = resource.resource_id
        self.snapshot_calls.append(resource_id)
        if self.snapshot_delay:
            await asyncio.sleep(self.snapshot_delay)
        error = self.snapshot_errors.get(resource_id)
        if error is not None:
            raise error
        dependencies = (
            self.dependencies.get(resource_id, set())
            if self.dependencies_in_snapshot
            else None
        )
        return ResourceSnapshot(
            resource_id,
            content=self.values[resource_id],
            dependencies=dependencies,
        )

    def _resource(self, resource_id: str) -> Resource:
        dependencies = (
            None
            if self.dependencies_in_snapshot
            else self.dependencies.get(resource_id, set())
        )
        return Resource(
            resource_id,
            metadata={"tags": self.tags.get(resource_id, [])},
            dependencies=dependencies,
            cheap_indicator=repr(self.values[resource_id]) if self.cheap else None,
            cheap_indicator_reliable=self.cheap,
        )


@dataclass
class RecordingOperation:
    """An operation that records calls and fails on demand.

    Attributes:
        calls: `(action, resource_id)` for every call, in call order.
        failures: Exceptions to raise, keyed by resource ID; raised on every call.
        failures_remaining: Number of times each ID in `failures` still fails;
            unlimited when absent.
        delay: Seconds each call sleeps.
        snapshots: Snapshot passed to each call, keyed by resource ID.
    """

    calls: list[tuple[PlanAction, str]] = field(default_factory=list)
    failures: dict[str, Exception] = field(default_factory=dict)
    failures_remaining: dict[str, int] = field(default_factory=dict)
    delay: float = 0.0
    snapshots: dict[str, ResourceSnapshot | None] = field(default_factory=dict)
    requests: list[RefreshRequest] = field(default_factory=list)

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        resource_id = resource.resource_id
        self.calls.append((action, resource_id))
        self.snapshots[resource_id] = snapshot
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        error = self.failures.get(resource_id)
        if error is None:
            return
        remaining = self.failures_remaining.get(resource_id)
        if remaining is not None:
            if remaining <= 0:
                return
            self.failures_remaining[resource_id] = remaining - 1
        raise error

    def ids(self, action: PlanAction | None = None) -> list[str]:
        """Return the IDs of recorded calls, optionally filtered by action."""
        return [
            resource_id
            for called, resource_id in self.calls
            if action is None or called is action
        ]


@dataclass
class EventRecorder:
    """A synchronous event handler that keeps every event."""

    events: list[RefreshEvent] = field(default_factory=list)

    def __call__(self, event: RefreshEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[EventKind]:
        """Return the kinds of the recorded events, in order."""
        return [event.kind for event in self.events]

    def of(self, kind: EventKind) -> list[RefreshEvent]:
        """Return the recorded events of one kind."""
        return [event for event in self.events if event.kind is kind]

    def attributes(self, kind: EventKind) -> Mapping[str, Any]:
        """Return the attributes of the only event of `kind`."""
        (event,) = self.of(kind)
        return event.attributes
