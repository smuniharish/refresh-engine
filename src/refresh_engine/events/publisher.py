"""Ordered, subscriber-isolated lifecycle event publication."""

from __future__ import annotations

import asyncio
import inspect
import threading

from refresh_engine.api.abc import EventPublisherABC
from refresh_engine.api.protocols import EventHandler
from refresh_engine.events.models import RefreshEvent


class EventPublisher(EventPublisherABC):
    """Queue ordered delivery without placing subscribers on the refresh path."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._lock = threading.Lock()
        self._errors: list[Exception] = []
        self._tail: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def errors(self) -> tuple[Exception, ...]:
        with self._lock:
            return tuple(self._errors)

    def subscribe(self, handler: EventHandler) -> None:
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        with self._lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    async def publish(self, event: RefreshEvent) -> None:
        previous = self._tail
        task = asyncio.create_task(
            self._dispatch_after(previous, event),
            name=f"refresh-event:{event.kind.value}",
        )
        self._tail = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await asyncio.sleep(0)

    async def drain(self) -> None:
        """Wait until all events published so far have been delivered."""
        tail = self._tail
        if tail is not None:
            await asyncio.shield(tail)

    async def _dispatch_after(
        self,
        previous: asyncio.Task[None] | None,
        event: RefreshEvent,
    ) -> None:
        if previous is not None:
            await previous
        with self._lock:
            handlers = tuple(self._handlers)
        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    result = await asyncio.to_thread(handler, event)
                    if inspect.isawaitable(result):
                        await result
            except asyncio.CancelledError:
                raise
            except Exception as error:
                with self._lock:
                    self._errors.append(error)
