"""Nominal abstract base classes for refresh-engine extension points.

The protocol layer supports structural typing. These ABCs are optional convenience
bases for consumers that prefer inheritance, abstract-method enforcement, and shared
lifecycle semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import TYPE_CHECKING

from refresh_engine.api.protocols import EventHandler
from refresh_engine.core.models import (
    DiscoveryResult,
    PlanAction,
    RefreshRequest,
    Resource,
    ResourceFingerprint,
    ResourceSnapshot,
    ResourceState,
)

if TYPE_CHECKING:
    from refresh_engine.events.models import RefreshEvent


class ResourceSourceABC(ABC):
    """Base class for asynchronous streaming resource sources."""

    @abstractmethod
    async def discover(self) -> DiscoveryResult:
        """Start a discovery and report whether it is complete."""

    @abstractmethod
    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        """Return observable state for one discovered resource."""


class FingerprintStrategyABC(ABC):
    """Base class for deterministic strong fingerprint strategies."""

    @abstractmethod
    async def fingerprint(self, snapshot: ResourceSnapshot) -> ResourceFingerprint:
        """Create a deterministic fingerprint."""


class RefreshOperationABC(ABC):
    """Base class for application-defined refresh side effects."""

    @abstractmethod
    async def __call__(
        self,
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        """Apply a refresh or deletion operation."""


class StateTransactionABC(ABC):
    """Base class for an isolated state transaction."""

    @abstractmethod
    async def put(self, state: ResourceState) -> None:
        """Stage a state upsert."""

    @abstractmethod
    async def delete(self, resource_id: str) -> None:
        """Stage a state deletion."""

    @abstractmethod
    async def commit(self) -> None:
        """Atomically commit staged changes."""

    @abstractmethod
    async def rollback(self) -> None:
        """Discard staged changes."""

    async def __aenter__(self) -> StateTransactionABC:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()


class StateStoreABC(ABC):
    """Base class for transactional state persistence."""

    @abstractmethod
    async def load_all(self) -> Mapping[str, ResourceState]:
        """Load the last successful state."""

    @abstractmethod
    def transaction(self) -> StateTransactionABC:
        """Create an isolated transaction."""


class ResourceSelectorABC(ABC):
    """Base class for reusable resource selection."""

    @abstractmethod
    def matches(self, resource: Resource) -> bool:
        """Return whether the resource is selected."""


class MetricsSinkABC(ABC):
    """Base class for provider-neutral metric sinks."""

    @abstractmethod
    def increment(
        self,
        name: str,
        value: int = 1,
        attributes: Mapping[str, str] | None = None,
    ) -> None:
        """Increment a counter."""

    @abstractmethod
    def observe(
        self,
        name: str,
        value: float,
        attributes: Mapping[str, str] | None = None,
    ) -> None:
        """Record a numeric observation."""


class EventPublisherABC(ABC):
    """Base class for lifecycle event transports."""

    @abstractmethod
    def subscribe(self, handler: EventHandler) -> None:
        """Register an event handler."""

    @abstractmethod
    def unsubscribe(self, handler: EventHandler) -> None:
        """Remove an event handler."""

    @abstractmethod
    async def publish(self, event: RefreshEvent) -> None:
        """Queue an event for delivery."""

    @abstractmethod
    async def drain(self) -> None:
        """Wait for queued event delivery."""


class AsyncSchedulerABC(ABC):
    """Base class for scheduler lifecycles owned by an event loop."""

    @abstractmethod
    async def start(self) -> None:
        """Start accepting scheduled work."""

    @abstractmethod
    async def pause(self) -> None:
        """Pause future scheduled work."""

    @abstractmethod
    async def resume(self) -> None:
        """Resume future scheduled work."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop and release owned tasks."""
