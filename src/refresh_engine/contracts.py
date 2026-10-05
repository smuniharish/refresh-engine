"""Extension contracts implemented by applications.

Each extension point is available in two styles:

- a [`typing.Protocol`][typing.Protocol] for structural typing, so any object with
  matching methods works without inheriting anything;
- an abstract base class that enforces the required methods and, where useful,
  supplies reusable behavior.

Protocol parameters are positional-only, so implementations may use any parameter
names.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

from refresh_engine.errors import StateStoreError

if TYPE_CHECKING:
    from refresh_engine.models import (
        DiscoveryResult,
        PlanAction,
        RefreshRequest,
        Resource,
        ResourceFingerprint,
        ResourceSnapshot,
        ResourceState,
    )

__all__ = [
    "FingerprintStrategy",
    "FingerprintStrategyABC",
    "MetricsSink",
    "MetricsSinkABC",
    "RefreshOperation",
    "RefreshOperationABC",
    "ResourceSelector",
    "ResourceSelectorABC",
    "ResourceSource",
    "ResourceSourceABC",
    "StateStore",
    "StateStoreABC",
    "StateTransaction",
    "StateTransactionABC",
]


@runtime_checkable
class ResourceSource(Protocol):
    """Discovers resources and reads their snapshots."""

    async def discover(self) -> DiscoveryResult:
        """Discover the current resources.

        Returns:
            The discovered resources and whether the listing is complete.

        Raises:
            Exception: Any exception marks the discovery as failed. The engine then
                performs no change detection and never infers deletions.
        """

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        """Read the observable state of one discovered resource.

        Args:
            resource: A resource returned by the current discovery.

        Returns:
            A snapshot whose `resource_id` matches `resource.resource_id`.

        Raises:
            Exception: Any exception marks only this resource as failed; its
                previous state is kept.
        """


@runtime_checkable
class RefreshOperation(Protocol):
    """Applies an application-defined side effect for one resource."""

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        """Refresh or delete one resource.

        Operations should be idempotent and must not suppress cancellation,
        because a retry, timeout, or restart can run them again.

        Args:
            resource: The discovered resource. For `PlanAction.DELETE` it carries
                only the ID of the deleted resource.
            snapshot: The current snapshot for `PlanAction.REFRESH`; `None` for
                `PlanAction.DELETE`.
            action: Whether to refresh or delete the resource.
            request: The request that planned the operation.

        Raises:
            Exception: Any exception marks the operation as failed, so the
                resource state is not advanced.
        """


@runtime_checkable
class FingerprintStrategy(Protocol):
    """Computes a deterministic fingerprint of a snapshot."""

    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        """Fingerprint a snapshot.

        Equal snapshots must always produce equal fingerprints, and every value that
        affects the refresh outcome must contribute to the fingerprint.

        Args:
            snapshot: The snapshot to fingerprint.

        Returns:
            The fingerprint of the snapshot.
        """


@runtime_checkable
class StateTransaction(Protocol):
    """Stages state changes and applies them atomically."""

    async def put(self, state: ResourceState, /) -> None:
        """Stage an insert or update of a resource state."""

    async def delete(self, resource_id: str, /) -> None:
        """Stage the removal of a resource state."""

    async def commit(self) -> None:
        """Apply every staged change atomically."""

    async def rollback(self) -> None:
        """Discard every staged change."""


@runtime_checkable
class StateStore(Protocol):
    """Persists the last successfully refreshed state of every resource."""

    async def load_all(self) -> Mapping[str, ResourceState]:
        """Return the last committed state of every resource, keyed by ID."""

    def transaction(self) -> StateTransaction:
        """Create a transaction for staging and committing changes."""


@runtime_checkable
class ResourceSelector(Protocol):
    """Selects resources by inspecting their discovery data."""

    def matches(self, resource: Resource, /) -> bool:
        """Return whether `resource` is selected."""


@runtime_checkable
class MetricsSink(Protocol):
    """Receives counters and numeric observations."""

    def increment(
        self,
        name: str,
        value: int = 1,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        """Add `value` to the counter `name`."""

    def observe(
        self,
        name: str,
        value: float,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        """Record one observation of `value` for the metric `name`."""


class ResourceSourceABC(ABC):
    """Base class for resource sources.

    See [`ResourceSource`][refresh_engine.ResourceSource] for the contract.
    """

    @abstractmethod
    async def discover(self) -> DiscoveryResult:
        """Discover the current resources."""

    @abstractmethod
    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        """Read the observable state of one discovered resource."""


class RefreshOperationABC(ABC):
    """Base class for refresh operations.

    See [`RefreshOperation`][refresh_engine.RefreshOperation] for the contract.
    """

    @abstractmethod
    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        """Refresh or delete one resource."""


class FingerprintStrategyABC(ABC):
    """Base class for fingerprint strategies.

    See [`FingerprintStrategy`][refresh_engine.FingerprintStrategy] for the
    contract.
    """

    __slots__ = ()

    @abstractmethod
    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        """Fingerprint a snapshot deterministically."""


class StateTransactionABC(ABC):
    """Base class for transactions that stage changes and apply them at once.

    `put` and `delete` stage changes in memory; `commit` passes them to
    [`apply_changes`][refresh_engine.StateTransactionABC.apply_changes], which
    subclasses implement atomically. A transaction is also an asynchronous context
    manager that commits on success and rolls back when the block raises.

    Subclasses that define `__init__` must call `super().__init__()`.

    Example:
        ```python
        class MyTransaction(StateTransactionABC):
            def __init__(self, database: Database) -> None:
                super().__init__()
                self._database = database

            async def apply_changes(self, puts, deletes, /) -> None:
                async with self._database.transaction():
                    await self._database.upsert_many(puts.values())
                    await self._database.delete_many(deletes)
        ```
    """

    def __init__(self) -> None:
        """Create an empty, open transaction."""
        self._staged_puts: dict[str, ResourceState] = {}
        self._staged_deletes: set[str] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether the transaction was committed or rolled back."""
        return self._closed

    async def put(self, state: ResourceState, /) -> None:
        """Stage an insert or update, replacing any staged change for the ID.

        Raises:
            StateStoreError: If the transaction is closed.
        """
        self._ensure_open()
        self._staged_deletes.discard(state.resource_id)
        self._staged_puts[state.resource_id] = state

    async def delete(self, resource_id: str, /) -> None:
        """Stage a removal, replacing any staged change for the ID.

        Raises:
            StateStoreError: If the transaction is closed.
        """
        self._ensure_open()
        self._staged_puts.pop(resource_id, None)
        self._staged_deletes.add(resource_id)

    async def commit(self) -> None:
        """Apply the staged changes and close the transaction.

        Nothing is applied when no change is staged. The transaction is closed even
        if applying the changes fails.

        Raises:
            StateStoreError: If the transaction is already closed.
        """
        self._ensure_open()
        self._closed = True
        puts = MappingProxyType(self._staged_puts)
        deletes = frozenset(self._staged_deletes)
        self._staged_puts = {}
        self._staged_deletes = set()
        if puts or deletes:
            await self.apply_changes(puts, deletes)

    async def rollback(self) -> None:
        """Discard the staged changes and close the transaction; safe to repeat."""
        self._closed = True
        self._staged_puts = {}
        self._staged_deletes = set()

    async def __aenter__(self) -> Self:
        """Return the transaction."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Commit when the block succeeds, otherwise roll back."""
        if self._closed:
            return
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()

    @abstractmethod
    async def apply_changes(
        self,
        puts: Mapping[str, ResourceState],
        deletes: AbstractSet[str],
        /,
    ) -> None:
        """Persist staged upserts and deletions atomically.

        Either every change is applied or none is.

        Args:
            puts: States to insert or update, keyed by resource ID.
            deletes: IDs of states to remove. Disjoint from the keys of `puts`.

        Raises:
            StateStoreError: If the changes cannot be applied.
        """

    def _ensure_open(self) -> None:
        if self._closed:
            raise StateStoreError("the state transaction is already closed")


class StateStoreABC(ABC):
    """Base class for state stores.

    See [`StateStore`][refresh_engine.StateStore] for the contract.
    """

    @abstractmethod
    async def load_all(self) -> Mapping[str, ResourceState]:
        """Return the last committed state of every resource, keyed by ID."""

    @abstractmethod
    def transaction(self) -> StateTransaction:
        """Create a transaction for staging and committing changes."""


class ResourceSelectorABC(ABC):
    """Base class for selectors that supports composition with operators.

    Selectors combine with `&` (all must match), `|` (any must match), and `~`
    (negation). Operands may be any object that satisfies
    [`ResourceSelector`][refresh_engine.ResourceSelector].

    Example:
        ```python
        selector = TagSelector(["critical"]) | IDSelector(["alpha"])
        stale = ~TagSelector(["archived"])
        ```
    """

    __slots__ = ()

    @abstractmethod
    def matches(self, resource: Resource, /) -> bool:
        """Return whether `resource` is selected."""

    def __and__(self, other: ResourceSelector) -> ResourceSelectorABC:
        """Select resources matched by both selectors."""
        if not _is_selector(other):
            return NotImplemented
        return _AllOf((*_parts(self, _AllOf), *_parts(other, _AllOf)))

    def __rand__(self, other: ResourceSelector) -> ResourceSelectorABC:
        """Select resources matched by both selectors."""
        if not _is_selector(other):
            return NotImplemented
        return _AllOf((*_parts(other, _AllOf), *_parts(self, _AllOf)))

    def __or__(self, other: ResourceSelector) -> ResourceSelectorABC:
        """Select resources matched by either selector."""
        if not _is_selector(other):
            return NotImplemented
        return selector_union(self, other)

    def __ror__(self, other: ResourceSelector) -> ResourceSelectorABC:
        """Select resources matched by either selector."""
        if not _is_selector(other):
            return NotImplemented
        return selector_union(other, self)

    def __invert__(self) -> ResourceSelectorABC:
        """Select resources that this selector does not match."""
        if isinstance(self, _Not):
            return _as_abc(self.selector)
        return _Not(self)


class MetricsSinkABC(ABC):
    """Base class for metric sinks.

    See [`MetricsSink`][refresh_engine.MetricsSink] for the contract.
    """

    @abstractmethod
    def increment(
        self,
        name: str,
        value: int = 1,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        """Add `value` to the counter `name`."""

    @abstractmethod
    def observe(
        self,
        name: str,
        value: float,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        """Record one observation of `value` for the metric `name`."""


def _is_selector(value: object) -> bool:
    return callable(getattr(value, "matches", None))


def selector_union(
    first: ResourceSelector, second: ResourceSelector
) -> ResourceSelectorABC:
    """Return a selector matching resources that either selector matches."""
    return _AnyOf((*_parts(first, _AnyOf), *_parts(second, _AnyOf)))


def _parts(
    selector: ResourceSelector, kind: type[_AllOf] | type[_AnyOf]
) -> tuple[ResourceSelector, ...]:
    if isinstance(selector, kind):
        return selector.selectors
    return (selector,)


def _as_abc(selector: ResourceSelector) -> ResourceSelectorABC:
    if isinstance(selector, ResourceSelectorABC):
        return selector
    return _AllOf((selector,))


@dataclass(frozen=True, slots=True)
class _AllOf(ResourceSelectorABC):
    selectors: tuple[ResourceSelector, ...]

    def matches(self, resource: Resource, /) -> bool:
        return all(selector.matches(resource) for selector in self.selectors)


@dataclass(frozen=True, slots=True)
class _AnyOf(ResourceSelectorABC):
    selectors: tuple[ResourceSelector, ...]

    def matches(self, resource: Resource, /) -> bool:
        return any(selector.matches(resource) for selector in self.selectors)


@dataclass(frozen=True, slots=True)
class _Not(ResourceSelectorABC):
    selector: ResourceSelector

    def matches(self, resource: Resource, /) -> bool:
        return not self.selector.matches(resource)
