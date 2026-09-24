"""Extension protocols for sources, operations, stores, and observability."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, runtime_checkable

from refresh_engine.core.models import (
    DiscoveryResult,
    PlanAction,
    RefreshRequest,
    Resource,
    ResourceFingerprint,
    ResourceSnapshot,
    ResourceState,
)


@runtime_checkable
class ResourceSource(Protocol):
    async def discover(self) -> DiscoveryResult:
        """Start a streaming discovery."""

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        """Return observable state for one resource."""


@runtime_checkable
class FingerprintStrategy(Protocol):
    async def fingerprint(self, snapshot: ResourceSnapshot) -> ResourceFingerprint:
        """Return a deterministic strong fingerprint."""


@runtime_checkable
class RefreshOperation(Protocol):
    async def __call__(
        self,
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        """Perform the application-defined refresh or deletion side effect."""


@runtime_checkable
class StateTransaction(Protocol):
    async def put(self, state: ResourceState) -> None: ...

    async def delete(self, resource_id: str) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def __aenter__(self) -> StateTransaction: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None: ...


@runtime_checkable
class StateStore(Protocol):
    async def load_all(self) -> Mapping[str, ResourceState]: ...

    def transaction(self) -> StateTransaction: ...


@runtime_checkable
class ResourceSelector(Protocol):
    def matches(self, resource: Resource) -> bool: ...


EventHandler = Callable[[object], Awaitable[None] | None]


@runtime_checkable
class MetricsSink(Protocol):
    def increment(
        self, name: str, value: int = 1, attributes: Mapping[str, str] | None = None
    ) -> None: ...

    def observe(
        self, name: str, value: float, attributes: Mapping[str, str] | None = None
    ) -> None: ...
