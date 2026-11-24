"""Lifecycle events and their asynchronous, in-order delivery.

The engine and scheduler publish [`RefreshEvent`][refresh_engine.RefreshEvent]
values to an [`EventPublisher`][refresh_engine.EventPublisher]. The default
[`AsyncEventPublisher`][refresh_engine.AsyncEventPublisher] delivers them to subscribed
handlers in a background task, so slow or failing handlers never delay or break a
refresh.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import threading
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from contextvars import Context, copy_context
from dataclasses import KW_ONLY, dataclass, field
from datetime import datetime
from enum import StrEnum, auto
from types import MethodType
from typing import Any, Final

from refresh_engine._internal.redaction import log_failure
from refresh_engine._internal.values import (
    Lifecycle,
    freeze_mapping,
    positive_int,
    utc_now,
)

__all__ = [
    "AsyncEventPublisher",
    "EventHandler",
    "EventHandlerCallable",
    "EventHandlerLike",
    "EventKind",
    "EventPublisher",
    "RefreshEvent",
]

_LOGGER: Final = logging.getLogger("refresh_engine")
_YIELD_EVERY: Final = 256
_next_sequence: Final = itertools.count().__next__


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


_BOUNDARY_EVENTS: Final = frozenset(
    {
        EventKind.REFRESH_STARTED,
        EventKind.REFRESH_COMPLETED,
        EventKind.REFRESH_FAILED,
        EventKind.REFRESH_CANCELLED,
        EventKind.REFRESH_SKIPPED,
        EventKind.SCHEDULER_STARTED,
        EventKind.SCHEDULER_STOPPED,
        EventKind.SCHEDULER_RUN_FAILED,
    }
)
_REFRESH_CLOSERS: Final = frozenset(
    {
        EventKind.REFRESH_COMPLETED,
        EventKind.REFRESH_FAILED,
        EventKind.REFRESH_CANCELLED,
        EventKind.REFRESH_SKIPPED,
    }
)
_RESOURCE_CLOSERS: Final = frozenset(
    {
        EventKind.RESOURCE_REFRESH_COMPLETED,
        EventKind.RESOURCE_REFRESH_FAILED,
        EventKind.RESOURCE_REFRESH_SKIPPED,
    }
)
_CLOSERS: Final = _REFRESH_CLOSERS | _RESOURCE_CLOSERS
_OPENERS: Final = frozenset(
    {EventKind.REFRESH_STARTED, EventKind.RESOURCE_REFRESH_STARTED}
)


def _merge(
    first: tuple[EventHandlerLike, ...], second: tuple[EventHandlerLike, ...]
) -> tuple[EventHandlerLike, ...]:
    """Return `first` followed by the handlers of `second` that it lacks."""
    if second is first or not second:
        return first
    if not first:
        return second
    extra = tuple(
        handler
        for handler in second
        if not any(_same_handler(handler, item) for item in first)
    )
    return (*first, *extra) if extra else first


class _Recipients:
    """Handlers that received the start of one tracked lifecycle.

    A resource lifecycle's recipients are also recorded on its refresh, whose
    terminal event closes the resources left open.
    """

    __slots__ = ("handlers", "parent")

    def __init__(self, parent: _Recipients | None = None) -> None:
        self.handlers: tuple[EventHandlerLike, ...] = ()
        self.parent = parent

    def record(self, handlers: tuple[EventHandlerLike, ...]) -> None:
        recipients: _Recipients | None = self
        while recipients is not None:
            recipients.handlers = _merge(recipients.handlers, handlers)
            recipients = recipients.parent


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
            [`EventKind`][refresh_engine.EventKind]. Built-in containers are
            copied and frozen at every depth.
        error: The exception behind a failure event, if any.

    Raises:
        ConfigurationError: If `attributes` contains a reference cycle.
    """

    kind: EventKind
    _: KW_ONLY
    refresh_id: str | None = None
    resource_id: str | None = None
    correlation_id: str | None = None
    timestamp: datetime = field(default_factory=utc_now)
    attributes: Mapping[str, Any] = field(default_factory=dict, hash=False)
    error: BaseException | None = field(default=None, compare=False, hash=False)
    _context: Context = field(default_factory=copy_context, compare=False, repr=False)
    # Creation order within the process; lets handlers recognize replayed events.
    _sequence: int = field(default_factory=_next_sequence, compare=False, repr=False)
    # Shared by every event the engine publishes for one refresh, so handlers can
    # tell its events apart from those of any other lifecycle without global order.
    _lifecycle: Lifecycle | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Freeze the attributes."""
        object.__setattr__(
            self, "attributes", freeze_mapping(self.attributes, "attributes")
        )


class EventHandler(ABC):
    """Base class for asynchronous event handlers."""

    @abstractmethod
    async def __call__(self, event: RefreshEvent) -> None:
        """Handle one event."""


type EventHandlerCallable = Callable[[RefreshEvent], Awaitable[object]]
"""An asynchronous function accepted directly as an event handler."""

type EventHandlerLike = EventHandler | EventHandlerCallable


class EventPublisher(ABC):
    """Contract for delivering lifecycle events to handlers."""

    @property
    def has_subscribers(self) -> bool:
        """Whether publishing an event can reach a handler.

        The engine skips building events while this is `False`. The default
        implementation always returns `True`.
        """
        return True

    @abstractmethod
    def subscribe(self, handler: EventHandlerLike) -> None:
        """Register `handler` to receive subsequently delivered events."""

    @abstractmethod
    def unsubscribe(self, handler: EventHandlerLike) -> None:
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


class AsyncEventPublisher(EventPublisher):
    """Deliver events to handlers in publication order from a background task.

    Every handler receives every delivered event in publication order, and
    handlers receive each event in subscription order. Handler exceptions are
    logged to the `refresh_engine` standard-library logger and kept in
    [`errors`][refresh_engine.AsyncEventPublisher.errors]; they never reach the code
    that published the event. Publishing is a no-op while there are no
    subscribers, except for the closing events described below.

    The queue of undelivered events is bounded, so a slow handler can neither
    block a refresh nor grow memory without limit. While `capacity` events are
    waiting, newly published events are dropped, except for refresh boundary
    events (`REFRESH_STARTED`, the terminal `REFRESH_*` events, and scheduler
    events), which may use up to
    [`BOUNDARY_RESERVE`][refresh_engine.AsyncEventPublisher.BOUNDARY_RESERVE]
    additional slots. Dropped events are counted in
    [`dropped`][refresh_engine.AsyncEventPublisher.dropped], and the first drop
    of each saturation episode is logged as a warning. Delivery is therefore
    best-effort under sustained overload.

    Lifecycles are never left half-delivered: once the publisher has queued a
    `REFRESH_STARTED` or `RESOURCE_REFRESH_STARTED` event, it always queues the
    event that closes it (the refresh's terminal event, or the resource's
    completed, failed, or skipped event), even when the queue is full. Handlers
    that pair start and end events, such as tracing handlers, therefore never
    lose the end of a lifecycle whose start they received. The publisher tracks
    at most `capacity` open refresh and resource lifecycles, so the queue holds
    at most `2 * capacity + BOUNDARY_RESERVE` events. A tracked lifecycle is
    never evicted: while `capacity` lifecycles are open, the start of a new
    refresh, and of a new resource within a tracked refresh, is not delivered.
    These rejected starts are counted in
    [`rejected_lifecycles`][refresh_engine.AsyncEventPublisher.rejected_lifecycles]
    and [`dropped`][refresh_engine.AsyncEventPublisher.dropped], and the first
    of each episode is logged as a warning. The other events of a refresh whose
    start was not delivered are still delivered on a best-effort basis, without
    the guarantee. Repeated start events of an open lifecycle are delivered
    without using capacity. A lifecycle is released when its closing event is
    published; a start published by application code that is never closed keeps
    its slot.

    The guarantee holds across unsubscribing: a handler that received the start
    of a lifecycle also receives its closing event, even if it unsubscribed in
    between, including when it was the last handler. Unsubscribing therefore
    stops delivery of everything else at once, but of a lifecycle's end only
    after that lifecycle closes. While lifecycles are open,
    [`has_subscribers`][refresh_engine.AsyncEventPublisher.has_subscribers]
    stays `True` so that the engine keeps publishing their closing events.

    If the background task is cancelled while a handler is running, that event
    is kept and delivered again by the next background task, which
    [`drain`][refresh_engine.AsyncEventPublisher.drain] or the next
    [`publish`][refresh_engine.AsyncEventPublisher.publish] starts: the
    interrupted handler receives it again from the start, and the handlers after
    it receive it for the first time. A handler interrupted this way can
    therefore see an event twice, and any side effects it performed before the
    interruption can be repeated. Handlers that already returned are not called
    again. A handler that raises `asyncio.CancelledError` while the background
    task is not being cancelled has failed; the failure is logged and delivery
    continues.

    Handlers run in the publisher's background task with a fresh context, so
    context variables of the code that published an event are not visible to
    them; correlate events with their `refresh_id` and `correlation_id`.

    `publish` and `drain` must be called from the event loop thread.
    `subscribe` and `unsubscribe` are thread-safe.

    Args:
        max_errors: Number of most recent handler exceptions to keep.
        capacity: Maximum number of undelivered events, not counting the reserve
            for boundary events.

    Raises:
        ConfigurationError: If `max_errors` or `capacity` is less than 1.
    """

    BOUNDARY_RESERVE: Final = 1024
    """Extra queue slots available only to refresh boundary events."""

    def __init__(self, *, max_errors: int = 100, capacity: int = 100_000) -> None:
        """Create a publisher without subscribers."""
        self._handlers: tuple[EventHandlerLike, ...] = ()
        self._lock = threading.Lock()
        self._capacity = positive_int(capacity, "capacity")
        self._queue: deque[tuple[RefreshEvent, _Recipients | None]] = deque()
        self._dispatcher: asyncio.Task[None] | None = None
        self._interrupted: (
            tuple[RefreshEvent, _Recipients | None, tuple[EventHandlerLike, ...]] | None
        ) = None
        self._lifecycles: dict[str, set[str]] = {}
        self._recipients: dict[tuple[str, str | None], _Recipients] = {}
        self._owed = 0
        self._tracked = 0
        self._rejected = 0
        self._rejecting = False
        self._errors: deque[Exception] = deque(
            maxlen=positive_int(max_errors, "max_errors")
        )
        self._dropped = 0
        self._peak = 0
        self._saturated = False

    @property
    def has_subscribers(self) -> bool:
        """Whether a handler is subscribed or a tracked lifecycle is still open.

        Open lifecycles keep this `True` after the last handler unsubscribes, so
        their closing events are still published to the handlers that received
        their start.
        """
        return bool(self._handlers or self._lifecycles)

    @property
    def errors(self) -> tuple[Exception, ...]:
        """The most recent exceptions raised by handlers, oldest first."""
        return tuple(self._errors)

    @property
    def capacity(self) -> int:
        """Maximum number of undelivered events, excluding the boundary reserve."""
        return self._capacity

    @property
    def queued(self) -> int:
        """Number of events published but not yet taken for delivery.

        An event whose delivery was interrupted is not counted; it is delivered
        first by the next background task.
        """
        return len(self._queue)

    @property
    def peak_queued(self) -> int:
        """Largest number of undelivered events observed so far."""
        return self._peak

    @property
    def dropped(self) -> int:
        """Number of events not delivered because the queue or tracking was full.

        Includes the starts counted in
        [`rejected_lifecycles`][refresh_engine.AsyncEventPublisher.rejected_lifecycles].
        """
        return self._dropped

    @property
    def open_lifecycles(self) -> int:
        """Number of refresh and resource lifecycles whose closing event is pending."""
        return self._tracked

    @property
    def rejected_lifecycles(self) -> int:
        """Number of lifecycle starts not delivered because tracking was full."""
        return self._rejected

    def subscribe(self, handler: EventHandlerLike) -> None:
        """Register `handler`; subscribing the same handler twice has no effect.

        Handlers are matched by identity; a bound method matches the same method
        of the same object.

        Raises:
            TypeError: If `handler` is not callable.
        """
        if not (
            isinstance(handler, EventHandler)
            or (callable(handler) and inspect.iscoroutinefunction(handler))
        ):
            raise TypeError(
                "event handlers must subclass EventHandler or be async functions"
            )
        with self._lock:
            if not any(_same_handler(item, handler) for item in self._handlers):
                self._handlers = (*self._handlers, handler)

    def unsubscribe(self, handler: EventHandlerLike) -> None:
        """Remove `handler`; removing an unknown handler has no effect."""
        with self._lock:
            self._handlers = tuple(
                item for item in self._handlers if not _same_handler(item, handler)
            )

    def publish(self, event: RefreshEvent) -> None:
        """Queue `event` for delivery to the current subscribers.

        Drops `event` instead when the queue is full; see the class description.

        Raises:
            RuntimeError: If called without a running event loop while there are
                subscribers.
        """
        kind = event.kind
        if kind in _CLOSERS and self._lifecycles:
            recipients = self._release(kind, event.refresh_id, event.resource_id)
            if recipients is not None:
                self._owed += 1
                self._enqueue(event, recipients)
                return
        if not self._handlers:
            return
        size = len(self._queue)
        limit = self._capacity
        if kind in _BOUNDARY_EVENTS:
            limit += self.BOUNDARY_RESERVE
        if size >= limit:
            self._dropped += 1
            if not self._saturated:
                self._saturated = True
                _LOGGER.warning(
                    "refresh event queue is full (capacity %d); dropping events "
                    "until handlers catch up",
                    self._capacity,
                )
            return
        recipients = None
        if kind in _OPENERS:
            admitted = self._admit(event)
            if admitted is False:
                self._dropped += 1
                self._rejected += 1
                if not self._rejecting:
                    self._rejecting = True
                    _LOGGER.warning(
                        "%d refresh lifecycles are open (capacity %d); not "
                        "delivering new lifecycle starts until some close",
                        self._tracked,
                        self._capacity,
                    )
                return
            if isinstance(admitted, _Recipients):
                recipients = admitted
        self._enqueue(event, recipients)

    async def drain(self) -> None:
        """Wait until every queued event has been delivered.

        Awaited directly by a handler, `drain` returns immediately; the remaining
        events are delivered after the handler returns. If delivery was
        interrupted, for example because the background task was cancelled, a new
        background task first delivers the interrupted event again, as described
        on the class, and then the remaining events. When no handler remains to
        receive them, events stay queued and `drain` returns. Dropped events are
        never delivered.
        """
        while True:
            dispatcher = self._dispatcher
            if dispatcher is None or dispatcher.done():
                if (not self._queue and self._interrupted is None) or not (
                    self._handlers or self._owed
                ):
                    return
                dispatcher = self._start()
            if dispatcher is asyncio.current_task():
                return
            await asyncio.wait((dispatcher,))

    def _enqueue(self, event: RefreshEvent, recipients: _Recipients | None) -> None:
        queue = self._queue
        queue.append((event, recipients))
        if len(queue) > self._peak:
            self._peak = len(queue)
        dispatcher = self._dispatcher
        if dispatcher is None or dispatcher.done():
            self._start()

    def _start(self) -> asyncio.Task[None]:
        dispatcher = asyncio.get_running_loop().create_task(
            self._dispatch(),
            name="refresh-engine-event-dispatcher",
            context=Context(),
        )
        self._dispatcher = dispatcher
        return dispatcher

    def _release(
        self, kind: EventKind, refresh_id: str | None, resource_id: str | None
    ) -> _Recipients | None:
        """Stop tracking the lifecycle a closing event ends.

        Returns the recipients of its start, or `None` if it was not tracked.
        """
        if refresh_id is None:
            return None
        recipients = self._recipients
        if kind in _REFRESH_CLOSERS:
            resources = self._lifecycles.pop(refresh_id, None)
            if resources is None:
                return None
            self._tracked -= 1 + len(resources)
            for open_resource in resources:
                del recipients[refresh_id, open_resource]
            return recipients.pop((refresh_id, None))
        resources = self._lifecycles.get(refresh_id)
        if resources is None or resource_id not in resources:
            return None
        resources.remove(resource_id)
        self._tracked -= 1
        return recipients.pop((refresh_id, resource_id))

    def _admit(self, event: RefreshEvent) -> _Recipients | bool:
        """Track the lifecycle an opening `event` starts.

        Returns the recipients to record the start's delivery on, `True` to
        deliver a start that needs no tracking, or `False` to reject a new
        lifecycle rather than evict an open one when tracking is full. Repeated
        starts of an open lifecycle share its recipients and use no capacity.
        """
        refresh_id = event.refresh_id
        if refresh_id is None:
            return True
        lifecycles = self._lifecycles
        recipients = self._recipients
        if event.kind is EventKind.REFRESH_STARTED:
            if refresh_id in lifecycles:
                return recipients[refresh_id, None]
            if self._tracked >= self._capacity:
                return False
            lifecycles[refresh_id] = set()
            admitted = recipients[refresh_id, None] = _Recipients()
        else:
            resources = lifecycles.get(refresh_id)
            resource_id = event.resource_id
            if resources is None or resource_id is None:
                return True
            if resource_id in resources:
                return recipients[refresh_id, resource_id]
            if self._tracked >= self._capacity:
                return False
            resources.add(resource_id)
            admitted = recipients[refresh_id, resource_id] = _Recipients(
                recipients[refresh_id, None]
            )
        self._tracked += 1
        self._rejecting = False
        return admitted

    async def _dispatch(self) -> None:
        queue = self._queue
        interrupted, self._interrupted = self._interrupted, None
        delivered = 0
        while True:
            if interrupted is not None:
                event, recipients, handlers = interrupted
                interrupted = None
                allowed = self._handlers
                if recipients is not None and event.kind in _CLOSERS:
                    allowed = _merge(recipients.handlers, allowed)
                handlers = tuple(
                    handler
                    for handler in handlers
                    if any(_same_handler(handler, item) for item in allowed)
                )
            elif queue:
                event, recipients = queue.popleft()
                handlers = self._handlers
                if recipients is not None:
                    if event.kind in _CLOSERS:
                        handlers = _merge(recipients.handlers, handlers)
                    else:
                        recipients.record(handlers)
            else:
                break
            for index, handler in enumerate(handlers):
                try:
                    await handler(event)
                except asyncio.CancelledError as cancellation:
                    task = asyncio.current_task()
                    if task is not None and task.cancelling():
                        self._interrupted = (event, recipients, handlers[index:])
                        raise
                    self._report(handler, event, cancellation)
                except Exception as error:
                    self._errors.append(error)
                    self._report(handler, event, error)
            if recipients is not None and event.kind in _CLOSERS:
                self._owed -= 1
            delivered += 1
            if delivered % _YIELD_EVERY == 0:
                await asyncio.sleep(0)
        self._saturated = False

    @staticmethod
    def _report(
        handler: EventHandlerLike, event: RefreshEvent, error: BaseException
    ) -> None:
        log_failure(
            _LOGGER,
            logging.WARNING,
            "refresh event handler %r failed for %s event",
            handler,
            event.kind.value,
            error=error,
        )
