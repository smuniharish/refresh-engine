"""Lifecycle events and their asynchronous, in-order delivery.

The engine and scheduler publish [`RefreshEvent`][refresh_engine.RefreshEvent]
values to an [`EventPublisherABC`][refresh_engine.EventPublisherABC]. The default
[`EventPublisher`][refresh_engine.EventPublisher] delivers them to subscribed
handlers in a background task, so slow or failing handlers never delay or break a
refresh.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextvars import Context
from dataclasses import KW_ONLY, dataclass, field
from datetime import datetime
from enum import StrEnum, auto
from types import MethodType
from typing import Any, Final

from refresh_engine._internal.values import (
    freeze_mapping,
    positive_int,
    utc_now,
)

__all__ = [
    "EventHandler",
    "EventKind",
    "EventPublisher",
    "EventPublisherABC",
    "RefreshEvent",
]

_LOGGER: Final = logging.getLogger("refresh_engine")
_YIELD_EVERY: Final = 256


def _same_handler(first: object, second: object) -> bool:
    if first is second:
        return True
    return (
        isinstance(first, MethodType)
        and isinstance(second, MethodType)
        and first.__self__ is second.__self__
        and first.__func__ is second.__func__
    )


class EventKind(StrEnum):
    """Kinds of lifecycle events."""

    REFRESH_STARTED = auto()
    """A refresh started. Attributes: `mode`, `trigger`, and `metadata` (the
    request metadata, when not empty)."""

    DISCOVERY_STARTED = auto()
    """Discovery started."""

    DISCOVERY_COMPLETED = auto()
    """Discovery finished. Attributes: `discovered`, `complete`, `duration`."""

    SNAPSHOT_STARTED = auto()
    """Snapshot reading and fingerprinting started. Attributes: `resources`."""

    RESOURCE_SNAPSHOT_FAILED = auto()
    """A snapshot or fingerprint failed for one resource. Attributes: `phase`,
    `error_type`."""

    SNAPSHOT_COMPLETED = auto()
    """Snapshot reading and fingerprinting finished. Attributes: `snapshotted`,
    `cheap_matches`, `failed`, `duration`."""

    DETECTION_COMPLETED = auto()
    """Changes were classified. Attributes: `added`, `modified`,
    `dependency_changed`, `unchanged`, `deleted`."""

    PLANNING_COMPLETED = auto()
    """The operation plan was built. Attributes: `refresh`, `delete`, `impacted`."""

    EXECUTION_STARTED = auto()
    """Operations started. Attributes: `operations`."""

    RESOURCE_REFRESH_STARTED = auto()
    """An operation started for one resource. Attributes: `action`."""

    RESOURCE_REFRESH_COMPLETED = auto()
    """An operation succeeded. Attributes: `action`, `attempts`, `duration`."""

    RESOURCE_REFRESH_FAILED = auto()
    """An operation failed after its final attempt. Attributes: `action`,
    `attempts`, `duration`, `phase`, `error_type`."""

    RESOURCE_REFRESH_SKIPPED = auto()
    """A planned operation did not run. Attributes: `action`, `reason`."""

    EXECUTION_COMPLETED = auto()
    """Operations finished. Attributes: `succeeded`, `failed`, `duration`."""

    STATE_PERSISTED = auto()
    """State changes were committed. Attributes: `written`, `deleted`, `duration`."""

    REFRESH_COMPLETED = auto()
    """A refresh finished with status `success` or `partial`."""

    REFRESH_FAILED = auto()
    """A refresh finished with status `failed`."""

    REFRESH_CANCELLED = auto()
    """A refresh was cancelled."""

    REFRESH_SKIPPED = auto()
    """A request was skipped because another refresh was running."""

    SCHEDULER_STARTED = auto()
    """A scheduler started. Attributes: `interval`."""

    SCHEDULER_STOPPED = auto()
    """A scheduler stopped."""

    SCHEDULER_RUN_FAILED = auto()
    """A scheduled callback raised an exception. Attributes: `error_type`."""


@dataclass(frozen=True, slots=True)
class RefreshEvent:
    """One lifecycle event.

    Terminal refresh events (`REFRESH_COMPLETED`, `REFRESH_FAILED`,
    `REFRESH_CANCELLED`, and `REFRESH_SKIPPED`) carry the attributes `status`,
    `mode`, `trigger`, `duration`, and the result counts.

    Attributes:
        kind: What happened.
        refresh_id: Refresh the event belongs to; `None` for scheduler events.
        resource_id: Affected resource, if any.
        correlation_id: Correlation ID of the originating request, if any.
        timestamp: When the event occurred, in UTC. Defaults to the current time.
        attributes: Event-specific values, documented on each
            [`EventKind`][refresh_engine.EventKind].
        error: The exception behind a failure event, if any.
    """

    kind: EventKind
    _: KW_ONLY
    refresh_id: str | None = None
    resource_id: str | None = None
    correlation_id: str | None = None
    timestamp: datetime = field(default_factory=utc_now)
    attributes: Mapping[str, Any] = field(default_factory=dict, hash=False)
    error: BaseException | None = field(default=None, compare=False, hash=False)

    def __post_init__(self) -> None:
        """Freeze the attributes."""
        object.__setattr__(self, "attributes", freeze_mapping(self.attributes))


type EventHandler = Callable[[RefreshEvent], Awaitable[None] | None]
"""A function or callable object that receives events.

Synchronous handlers run on the event loop and must return quickly. Handlers that
return an awaitable, such as `async def` functions, are awaited before the next
handler runs.
"""


class EventPublisherABC(ABC):
    """Contract for delivering lifecycle events to handlers."""

    @property
    def has_subscribers(self) -> bool:
        """Whether publishing an event can reach a handler.

        The engine skips building events while this is `False`. The default
        implementation always returns `True`.
        """
        return True

    @abstractmethod
    def subscribe(self, handler: EventHandler) -> None:
        """Register `handler` to receive subsequently delivered events."""

    @abstractmethod
    def unsubscribe(self, handler: EventHandler) -> None:
        """Stop delivering events to `handler`."""

    @abstractmethod
    def publish(self, event: RefreshEvent) -> None:
        """Schedule delivery of `event` without blocking or raising handler errors.

        Called from the event loop thread.
        """

    @abstractmethod
    async def drain(self) -> None:
        """Wait until every event published so far has been delivered.

        Implementations must return without waiting when awaited directly by a
        handler, which would otherwise wait for its own delivery.
        """


class EventPublisher(EventPublisherABC):
    """Deliver events to handlers in publication order from a background task.

    Every handler receives every event in publication order, and handlers receive
    each event in subscription order. Handler exceptions are logged to the
    `refresh_engine` standard-library logger and kept in
    [`errors`][refresh_engine.EventPublisher.errors]; they never reach the code
    that published the event. Publishing is a no-op while there are no
    subscribers.

    Handlers run in the publisher's background task with a fresh context, so
    context variables of the code that published an event are not visible to
    them; correlate events with their `refresh_id` and `correlation_id`.

    `publish` and `drain` must be called from the event loop thread.
    `subscribe` and `unsubscribe` are thread-safe.

    Args:
        max_errors: Number of most recent handler exceptions to keep.

    Raises:
        ConfigurationError: If `max_errors` is less than 1.
    """

    def __init__(self, *, max_errors: int = 100) -> None:
        """Create a publisher without subscribers."""
        self._handlers: tuple[EventHandler, ...] = ()
        self._lock = threading.Lock()
        self._queue: deque[RefreshEvent] = deque()
        self._dispatcher: asyncio.Task[None] | None = None
        self._errors: deque[Exception] = deque(
            maxlen=positive_int(max_errors, "max_errors")
        )

    @property
    def has_subscribers(self) -> bool:
        """Whether at least one handler is subscribed."""
        return bool(self._handlers)

    @property
    def errors(self) -> tuple[Exception, ...]:
        """The most recent exceptions raised by handlers, oldest first."""
        return tuple(self._errors)

    def subscribe(self, handler: EventHandler) -> None:
        """Register `handler`; subscribing the same handler twice has no effect.

        Handlers are matched by identity; a bound method matches the same method
        of the same object.

        Raises:
            TypeError: If `handler` is not callable.
        """
        if not callable(handler):
            raise TypeError("event handlers must be callable")
        with self._lock:
            if not any(_same_handler(item, handler) for item in self._handlers):
                self._handlers = (*self._handlers, handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        """Remove `handler`; removing an unknown handler has no effect."""
        with self._lock:
            self._handlers = tuple(
                item for item in self._handlers if not _same_handler(item, handler)
            )

    def publish(self, event: RefreshEvent) -> None:
        """Queue `event` for delivery to the current subscribers.

        Raises:
            RuntimeError: If called without a running event loop while there are
                subscribers.
        """
        if not self._handlers:
            return
        self._queue.append(event)
        dispatcher = self._dispatcher
        if dispatcher is None or dispatcher.done():
            self._dispatcher = asyncio.get_running_loop().create_task(
                self._dispatch(),
                name="refresh-engine-event-dispatcher",
                context=Context(),
            )

    async def drain(self) -> None:
        """Wait until every queued event has been delivered.

        Awaited directly by a handler, `drain` returns immediately; the remaining
        events are delivered after the handler returns.
        """
        while (dispatcher := self._dispatcher) is not None and not dispatcher.done():
            if dispatcher is asyncio.current_task():
                return
            await asyncio.wait((dispatcher,))

    async def _dispatch(self) -> None:
        delivered = 0
        while self._queue:
            event = self._queue.popleft()
            for handler in self._handlers:
                try:
                    outcome = handler(event)
                    if outcome is not None and inspect.isawaitable(outcome):
                        await outcome
                except Exception as error:
                    self._errors.append(error)
                    _LOGGER.warning(
                        "refresh event handler %r failed for %s event",
                        handler,
                        event.kind.value,
                        exc_info=error,
                    )
            delivered += 1
            if delivered % _YIELD_EVERY == 0:
                await asyncio.sleep(0)
