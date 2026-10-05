"""Resource discovery and observation (snapshot plus fingerprint)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Awaitable

from refresh_engine._internal.context import Observation, classify
from refresh_engine.contracts import FingerprintStrategy, ResourceSource
from refresh_engine.errors import DiscoveryError, FingerprintError, SnapshotError
from refresh_engine.models import (
    DiscoveryResult,
    RefreshPhase,
    Resource,
    ResourceFingerprint,
    ResourceSnapshot,
    ResourceState,
)


async def bounded[T](awaitable: Awaitable[T], seconds: float | None, action: str) -> T:
    """Await `awaitable`, failing with a descriptive `TimeoutError` on expiry."""
    if seconds is None:
        return await awaitable
    scope = asyncio.timeout(seconds)
    try:
        async with scope:
            return await awaitable
    except TimeoutError as error:
        if scope.expired():
            raise TimeoutError(
                f"{action} timed out after {seconds:g} seconds"
            ) from error
        raise


type Discovery = tuple[dict[str, Resource], bool, tuple[str, ...]]


async def discover(source: ResourceSource, seconds: float | None) -> Discovery:
    """Run and fully consume one discovery."""
    return await bounded(_consume(source), seconds, "discovery")


async def _consume(source: ResourceSource) -> Discovery:
    result = await source.discover()
    if not isinstance(result, DiscoveryResult):
        raise DiscoveryError(
            f"discover() returned {type(result).__name__}; expected DiscoveryResult"
        )
    resources: dict[str, Resource] = {}
    items = result.resources
    if isinstance(items, AsyncIterable):
        async for resource in items:
            _add(resources, resource)
    else:
        for resource in items:
            _add(resources, resource)
    return resources, result.complete, result.diagnostics


def _add(resources: dict[str, Resource], resource: object) -> None:
    if not isinstance(resource, Resource):
        raise DiscoveryError(
            f"discovery yielded {type(resource).__name__}; expected Resource"
        )
    if resource.resource_id in resources:
        raise DiscoveryError(f"duplicate resource ID: {resource.resource_id!r}")
    resources[resource.resource_id] = resource


def cheap_match(
    resource: Resource, previous: ResourceState | None
) -> Observation | None:
    """Observe `resource` from its previous state if its reliable indicator matches."""
    if (
        previous is None
        or not resource.cheap_indicator_reliable
        or resource.cheap_indicator is None
        or resource.cheap_indicator != previous.cheap_indicator
    ):
        return None
    dependencies = (
        frozenset(resource.dependencies)
        if resource.dependencies is not None
        else frozenset(previous.dependencies)
    )
    return Observation(
        resource=resource,
        fingerprint=previous.fingerprint,
        version=resource.version if resource.version is not None else previous.version,
        metadata=previous.snapshot_metadata,
        dependencies=dependencies,
        previous=previous,
        change=classify(previous, previous.fingerprint, dependencies),
    )


class ObservationError(Exception):
    """A snapshot or fingerprint failed for one resource."""

    def __init__(self, phase: RefreshPhase, error: Exception) -> None:
        super().__init__(str(error))
        self.phase = phase
        self.error = error


class Observer:
    """Read and fingerprint snapshots."""

    __slots__ = ("_source", "_strategy", "_timeout")

    def __init__(
        self,
        source: ResourceSource,
        strategy: FingerprintStrategy,
        timeout: float | None,
    ) -> None:
        self._source = source
        self._strategy = strategy
        self._timeout = timeout

    async def observe(
        self, resource: Resource, previous: ResourceState | None
    ) -> tuple[ResourceSnapshot, Observation]:
        """Snapshot and fingerprint one resource.

        Raises:
            ObservationError: If the snapshot or fingerprint fails.
        """
        resource_id = resource.resource_id
        try:
            snapshot = await bounded(
                self._source.snapshot(resource), self._timeout, "snapshot"
            )
            if not isinstance(snapshot, ResourceSnapshot):
                raise SnapshotError(
                    f"snapshot() returned {type(snapshot).__name__}; "
                    "expected ResourceSnapshot"
                )
            if snapshot.resource_id != resource_id:
                raise SnapshotError(
                    f"snapshot() returned resource ID {snapshot.resource_id!r} "
                    f"for {resource_id!r}"
                )
        except Exception as error:
            raise ObservationError(RefreshPhase.SNAPSHOT, error) from error
        try:
            fingerprint = await self._strategy.fingerprint(snapshot)
            if not isinstance(fingerprint, ResourceFingerprint):
                raise FingerprintError(
                    f"fingerprint() returned {type(fingerprint).__name__}; "
                    "expected ResourceFingerprint"
                )
        except Exception as error:
            raise ObservationError(RefreshPhase.FINGERPRINT, error) from error
        if snapshot.dependencies is not None:
            dependencies = frozenset(snapshot.dependencies)
        elif resource.dependencies is not None:
            dependencies = frozenset(resource.dependencies)
        else:
            dependencies = frozenset()
        observation = Observation(
            resource=resource,
            fingerprint=fingerprint,
            version=snapshot.version
            if snapshot.version is not None
            else resource.version,
            metadata=snapshot.metadata,
            dependencies=dependencies,
            previous=previous,
            change=classify(previous, fingerprint, dependencies),
        )
        return snapshot, observation
