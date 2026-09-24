"""Asyncio-native interval scheduler."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum, auto

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from refresh_engine.api.abc import AsyncSchedulerABC
from refresh_engine.errors import SchedulerError
from refresh_engine.events import EventKind, EventPublisher, RefreshEvent

ScheduledCallback = Callable[[], Awaitable[object]]


class SchedulerState(StrEnum):
    STOPPED = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()


class AsyncScheduler(AsyncSchedulerABC):
    def __init__(
        self,
        callback: ScheduledCallback,
        interval: float,
        *,
        events: EventPublisher | None = None,
        run_immediately: bool = False,
        cancel_active_on_stop: bool = False,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._callback = callback
        self._interval = interval
        self._events = events or EventPublisher()
        self._run_immediately = run_immediately
        self._cancel_active_on_stop = cancel_active_on_stop
        self._state = SchedulerState.STOPPED
        self._task: asyncio.Task[None] | None = None
        self._active: asyncio.Task[object] | None = None
        self._wake = asyncio.Event()
        self._timer: AsyncIOScheduler | None = None

    @property
    def state(self) -> SchedulerState:
        return self._state

    async def start(self) -> None:
        if self._state is not SchedulerState.STOPPED:
            raise SchedulerError("scheduler is already started")
        self._state = SchedulerState.RUNNING
        self._timer = AsyncIOScheduler(event_loop=asyncio.get_running_loop())
        self._timer.add_job(
            self._wake.set,
            "interval",
            seconds=self._interval,
            id="refresh-engine-interval",
            coalesce=True,
            max_instances=1,
        )
        self._timer.start()
        self._task = asyncio.create_task(
            self._run(), name="refresh-engine-async-scheduler"
        )
        if self._run_immediately:
            self._wake.set()
        await self._events.publish(RefreshEvent(EventKind.SCHEDULER_STARTED))

    async def pause(self) -> None:
        if self._state is not SchedulerState.RUNNING:
            raise SchedulerError("only a running scheduler can be paused")
        self._state = SchedulerState.PAUSED
        assert self._timer is not None
        self._timer.pause()

    async def resume(self) -> None:
        if self._state is not SchedulerState.PAUSED:
            raise SchedulerError("only a paused scheduler can be resumed")
        self._state = SchedulerState.RUNNING
        assert self._timer is not None
        self._timer.resume()

    async def stop(self) -> None:
        if self._state is SchedulerState.STOPPED:
            return
        self._state = SchedulerState.STOPPING
        if self._timer is not None:
            self._timer.shutdown(wait=False)
        self._wake.set()
        if self._active is not None and self._cancel_active_on_stop:
            self._active.cancel()
        if self._task is not None:
            await self._task
        self._state = SchedulerState.STOPPED
        self._timer = None
        await self._events.publish(RefreshEvent(EventKind.SCHEDULER_STOPPED))

    async def _run(self) -> None:
        try:
            while self._state is not SchedulerState.STOPPING:
                await self._wake.wait()
                self._wake.clear()
                if self._state is not SchedulerState.RUNNING:
                    continue
                self._active = asyncio.create_task(
                    self._invoke_callback(), name="refresh-engine-scheduled-refresh"
                )
                try:
                    await self._active
                except asyncio.CancelledError:
                    if self._state is not SchedulerState.STOPPING:
                        raise
                finally:
                    self._active = None
        finally:
            if self._active is not None and not self._active.done():
                if self._cancel_active_on_stop:
                    self._active.cancel()
                await asyncio.gather(self._active, return_exceptions=True)

    async def _invoke_callback(self) -> object:
        return await self._callback()
