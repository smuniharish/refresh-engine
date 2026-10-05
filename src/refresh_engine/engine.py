"""The refresh engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import TracebackType
from typing import Any, Self

from refresh_engine._internal.coordinator import RefreshCoordinator
from refresh_engine._internal.pipeline import RefreshPipeline
from refresh_engine._internal.reentrancy import awaited_by
from refresh_engine._internal.values import id_set
from refresh_engine.contracts import (
    FingerprintStrategy,
    RefreshOperation,
    ResourceSelector,
    ResourceSource,
    StateStore,
)
from refresh_engine.events import EventPublisher, EventPublisherABC
from refresh_engine.fingerprints import CompositeHash
from refresh_engine.models import (
    RefreshConfig,
    RefreshMode,
    RefreshRequest,
    RefreshResult,
    TriggerSource,
)
from refresh_engine.stores import InMemoryStateStore

__all__ = ["RefreshEngine"]


class RefreshEngine:
    """Detect changes in a population of resources and refresh what changed.

    Every refresh loads the previous state, discovers resources, observes them
    (reading a snapshot and fingerprinting it, or reusing the previous state when a
    reliable cheap indicator is unchanged), classifies changes, plans operations in
    dependency order, runs them with bounded concurrency, and commits the state of
    each successful resource.

    An engine runs one refresh at a time; concurrent requests are handled by
    `config.overlap_policy`. Use the engine from a single event loop and close it
    when done, preferably with `async with`.

    The source, operation, fingerprint strategy, and state store of a refresh run
    in a copy of the context of the code that requested it, so context variables,
    such as tracing spans or bound log fields, reach them. Requests merged by the
    overlap policy run in the context of the first request.

    Args:
        source: Discovers resources and reads their snapshots.
        operation: Applies the refresh or delete side effect of one resource.
        fingerprint: Detects content changes; defaults to
            [`CompositeHash`][refresh_engine.CompositeHash].
        store: Persists resource state; defaults to a new
            [`InMemoryStateStore`][refresh_engine.InMemoryStateStore].
        config: Execution settings; defaults to
            [`RefreshConfig()`][refresh_engine.RefreshConfig].
        events: Publisher for lifecycle events; defaults to a new
            [`EventPublisher`][refresh_engine.EventPublisher].

    Raises:
        TypeError: If an argument does not implement its contract.

    Example:
        ```python
        async with RefreshEngine(source, operation) as engine:
            result = await engine.refresh()
            print(result.status, result.refreshed_count)
        ```
    """

    def __init__(
        self,
        source: ResourceSource,
        operation: RefreshOperation,
        *,
        fingerprint: FingerprintStrategy | None = None,
        store: StateStore | None = None,
        config: RefreshConfig | None = None,
        events: EventPublisherABC | None = None,
    ) -> None:
        """Create an engine."""
        fingerprint = CompositeHash() if fingerprint is None else fingerprint
        store = InMemoryStateStore() if store is None else store
        config = RefreshConfig() if config is None else config
        events = EventPublisher() if events is None else events
        if not isinstance(source, ResourceSource):
            raise TypeError("source must provide discover() and snapshot()")
        if not callable(operation):
            raise TypeError("operation must be callable")
        if not isinstance(fingerprint, FingerprintStrategy):
            raise TypeError("fingerprint must provide fingerprint()")
        if not isinstance(store, StateStore):
            raise TypeError("store must provide load_all() and transaction()")
        if not isinstance(config, RefreshConfig):
            raise TypeError("config must be a RefreshConfig")
        if not isinstance(events, EventPublisherABC):
            raise TypeError("events must be an EventPublisherABC")
        self._source = source
        self._operation = operation
        self._fingerprint = fingerprint
        self._store = store
        self._config = config
        self._events = events
        pipeline = RefreshPipeline(
            source, operation, fingerprint, store, config, events
        )
        self._coordinator = RefreshCoordinator(
            pipeline.run, pipeline.finish_unrun, config.overlap_policy
        )

    @property
    def source(self) -> ResourceSource:
        """The resource source."""
        return self._source

    @property
    def operation(self) -> RefreshOperation:
        """The refresh operation."""
        return self._operation

    @property
    def fingerprint(self) -> FingerprintStrategy:
        """The fingerprint strategy."""
        return self._fingerprint

    @property
    def store(self) -> StateStore:
        """The state store."""
        return self._store

    @property
    def config(self) -> RefreshConfig:
        """The execution settings."""
        return self._config

    @property
    def events(self) -> EventPublisherABC:
        """The lifecycle event publisher."""
        return self._events

    @property
    def closed(self) -> bool:
        """Whether the engine rejects new requests."""
        return self._coordinator.closed

    async def refresh(
        self,
        *,
        mode: RefreshMode | None = None,
        resource_ids: str | Iterable[str] = (),
        selector: ResourceSelector | None = None,
        trigger: TriggerSource = TriggerSource.MANUAL,
        metadata: Mapping[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> RefreshResult:
        """Run a refresh and return its result.

        Failures of discovery, snapshots, operations, and the state store are
        reported in the result instead of being raised.

        Args:
            mode: Which resources to refresh. Defaults to `TARGETED` when
                `resource_ids` or `selector` is given, otherwise `INCREMENTAL`.
            resource_ids: IDs to refresh in `TARGETED` mode. A single string is
                treated as one ID.
            selector: Selector evaluated against discovered resources in
                `TARGETED` mode.
            trigger: What initiated the refresh.
            metadata: Application metadata attached to the request.
            correlation_id: Identifier propagated to every lifecycle event.

        Returns:
            The refresh result.

        Raises:
            ConfigurationError: If the arguments are inconsistent.
            EngineClosedError: If the engine is closing or closed.
            RuntimeError: If awaited directly by the source, operation, fingerprint
                strategy, or state store of one of this engine's refreshes, which
                would wait for itself.
        """
        ids = id_set(resource_ids, "resource_ids")
        if mode is None:
            mode = (
                RefreshMode.TARGETED
                if ids or selector is not None
                else RefreshMode.INCREMENTAL
            )
        request = RefreshRequest(
            mode=mode,
            resource_ids=ids,
            selector=selector,
            trigger=trigger,
            metadata=metadata or {},
            correlation_id=correlation_id,
        )
        return await self._coordinator.submit(request)

    async def submit(self, request: RefreshRequest) -> RefreshResult:
        """Run a prepared request and return its result.

        Args:
            request: The request to run.

        Returns:
            The refresh result.

        Raises:
            EngineClosedError: If the engine is closing or closed.
            RuntimeError: If awaited directly by the source, operation, fingerprint
                strategy, or state store of one of this engine's refreshes, which
                would wait for itself.
            TypeError: If `request` is not a `RefreshRequest`.
        """
        if not isinstance(request, RefreshRequest):
            raise TypeError("request must be a RefreshRequest")
        return await self._coordinator.submit(request)

    async def close(self, *, cancel: bool = False, wait: bool = True) -> None:
        """Stop accepting requests, finish accepted ones, and deliver pending events.

        Closing twice is safe.

        Args:
            cancel: Cancel the running and queued refreshes instead of letting them
                finish; their callers receive `CANCELLED` results.
            wait: Wait until the accepted refreshes have finished and their events
                have been delivered. Pass `False` to close from code that a refresh
                or an event handler waits for. Awaited directly by the source,
                operation, fingerprint strategy, or state store of one of this
                engine's refreshes, `close` never waits.
        """
        waiting = wait and not awaited_by(self._coordinator)
        await self._coordinator.close(cancel=cancel, wait=waiting)
        if waiting:
            await self._events.drain()

    async def __aenter__(self) -> Self:
        """Return the engine."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the engine, cancelling active work if the block raised."""
        await self.close(cancel=exc_type is not None)
