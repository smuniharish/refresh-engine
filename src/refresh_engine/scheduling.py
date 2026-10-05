"""Interval scheduling and bridges for externally owned schedulers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import math
from collections.abc import Awaitable, Callable
from enum import StrEnum, auto
from types import TracebackType
from typing import Final, Self

from refresh_engine._internal.values import number
from refresh_engine.errors import SchedulerError
from refresh_engine.events import (
    EventKind,
    EventPublisher,
    EventPublisherABC,
    RefreshEvent,
)
from refresh_engine.models import RefreshRequest, RefreshResult, TriggerSource

__all__ = ["AsyncScheduler", "ExternalSchedulerAdapter", "SchedulerState"]

_LOGGER: Final = logging.getLogger("refresh_engine")


class SchedulerState(StrEnum):
    """Lifecycle state of an [`AsyncScheduler`][refresh_engine.AsyncScheduler]."""

    STOPPED = auto()
    """Not started, or fully stopped."""

    RUNNING = auto()
    """Invoking the callback on every interval."""

    PAUSED = auto()
    """Started, but skipping scheduled runs until resumed."""

    STOPPING = auto()
    """Stopping; waiting for an active callback to finish."""


class AsyncScheduler:
    """Run an async callback at a fixed interval on the running event loop.

    Runs start on a fixed grid of `interval` seconds measured from `start()`. A run
    that takes longer than the interval skips the missed slots instead of running
    back to back, and runs never overlap. A callback exception is logged to the
    `refresh_engine` standard-library logger, published as a
    `SCHEDULER_RUN_FAILED` event, and does not stop the schedule.

    The scheduler is an asynchronous context manager: it starts on entry and stops
    on exit, cancelling an active run if the block raised.

    Args:
        callback: Zero-argument function returning an awaitable, typically
            `lambda: engine.refresh(trigger=TriggerSource.SCHEDULED)`.
        interval: Seconds between scheduled runs.
        events: Publisher for scheduler events.
        run_immediately: Run the callback as soon as the scheduler starts instead of
            after the first interval.

    Raises:
        ConfigurationError: If `interval` is not a positive finite number.
        TypeError: If `callback` is not callable.

    Example:
        ```python
        scheduler = AsyncScheduler(
            lambda: engine.refresh(trigger=TriggerSource.SCHEDULED),
            interval=300,
            run_immediately=True,
        )
        async with engine, scheduler:
            await shutdown_requested.wait()
        ```
    """

    def __init__(
        self,
        callback: Callable[[], Awaitable[object]],
        interval: float,
        *,
        events: EventPublisherABC | None = None,
        run_immediately: bool = False,
    ) -> None:
        """Create a stopped scheduler."""
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback
        self._interval = number(interval, "interval", minimum=0, exclusive=True)
        self._events = events if events is not None else EventPublisher()
        self._run_immediately = run_immediately
        self._state = SchedulerState.STOPPED
        self._task: asyncio.Task[None] | None = None
        self._active: asyncio.Task[object] | None = None
        self._wakeup = asyncio.Event()

    @property
    def state(self) -> SchedulerState:
        """Current lifecycle state."""
        return self._state

    @property
    def interval(self) -> float:
        """Seconds between scheduled runs."""
        return self._interval

    async def start(self) -> None:
        """Start the schedule and return without waiting for a run.

        Raises:
            SchedulerError: If the scheduler is not stopped.
        """
        if self._state is not SchedulerState.STOPPED:
            raise SchedulerError("the scheduler is already started")
        self._state = SchedulerState.RUNNING
        self._wakeup = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(
            self._run(), name="refresh-engine-scheduler"
        )
        self._publish(EventKind.SCHEDULER_STARTED, {"interval": self._interval})

    async def pause(self) -> None:
        """Skip scheduled runs until resumed; an active run continues.

        Raises:
            SchedulerError: If the scheduler is not running.
        """
        if self._state is not SchedulerState.RUNNING:
            raise SchedulerError("only a running scheduler can be paused")
        self._state = SchedulerState.PAUSED
        self._wakeup.set()

    async def resume(self) -> None:
        """Resume scheduled runs at the next interval boundary.

        Raises:
            SchedulerError: If the scheduler is not paused.
        """
        if self._state is not SchedulerState.PAUSED:
            raise SchedulerError("only a paused scheduler can be resumed")
        self._state = SchedulerState.RUNNING
        self._wakeup.set()

    async def stop(self, *, cancel: bool = False, wait: bool = True) -> None:
        """Stop the schedule.

        Stopping a stopped scheduler has no effect, and concurrent calls are safe.

        Args:
            cancel: Cancel an active run instead of letting it finish.
            wait: Wait until the scheduler has stopped, including an active run.
                Pass `False` to stop from code that a scheduled run may wait for,
                such as an operation of the engine that the scheduler refreshes.
                Called from the scheduled callback itself, `stop` never waits.
        """
        task = self._task
        if task is None:
            return
        self._state = SchedulerState.STOPPING
        self._wakeup.set()
        active = self._active
        if cancel and active is not None:
            active.cancel()
        if not wait or (active is not None and active is asyncio.current_task()):
            return
        await asyncio.wait((task,))

    async def __aenter__(self) -> Self:
        """Start the scheduler."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the scheduler, cancelling an active run if the block raised."""
        await self.stop(cancel=exc_type is not None)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        origin = loop.time()
        due = origin if self._run_immediately else origin + self._interval
        try:
            while self._state is not SchedulerState.STOPPING:
                if self._state is SchedulerState.PAUSED:
                    await self._sleep(None)
                    due = self._next_slot(origin, loop.time(), inclusive=True)
                    continue
                delay = due - loop.time()
                if delay > 0:
                    await self._sleep(delay)
                    continue
                await self._invoke()
                due = self._next_slot(origin, loop.time(), inclusive=False)
        finally:
            self._task = None
            self._active = None
            self._state = SchedulerState.STOPPED
            self._publish(EventKind.SCHEDULER_STOPPED, {})

    def _next_slot(self, origin: float, now: float, *, inclusive: bool) -> float:
        elapsed = (now - origin) / self._interval
        slots = math.ceil(elapsed) if inclusive else math.floor(elapsed) + 1
        return origin + max(slots, 1) * self._interval

    async def _sleep(self, delay: float | None) -> None:
        try:
            async with asyncio.timeout(delay):
                await self._wakeup.wait()
        except TimeoutError:
            pass
        self._wakeup.clear()

    async def _invoke(self) -> None:
        active = asyncio.get_running_loop().create_task(
            self._call(), name="refresh-engine-scheduled-run"
        )
        self._active = active
        try:
            await asyncio.wait((active,))
        finally:
            self._active = None
        if active.cancelled():
            return
        error = active.exception()
        if error is not None:
            _LOGGER.error("scheduled refresh callback failed", exc_info=error)
            self._publish(
                EventKind.SCHEDULER_RUN_FAILED,
                {"error_type": type(error).__name__},
                error=error,
            )

    async def _call(self) -> object:
        return await self._callback()

    def _publish(
        self,
        kind: EventKind,
        attributes: dict[str, object],
        *,
        error: BaseException | None = None,
    ) -> None:
        if not self._events.has_subscribers:
            return
        try:
            self._events.publish(RefreshEvent(kind, attributes=attributes, error=error))
        except Exception:
            _LOGGER.exception("failed to publish %s event", kind.value)


class ExternalSchedulerAdapter:
    """Submit refresh requests from an externally owned scheduler.

    Use [`run`][refresh_engine.ExternalSchedulerAdapter.run] from coroutines on the
    engine's event loop, and
    [`run_threadsafe`][refresh_engine.ExternalSchedulerAdapter.run_threadsafe] or
    [`submit_threadsafe`][refresh_engine.ExternalSchedulerAdapter.submit_threadsafe]
    from other threads, such as a thread-based job scheduler.

    Args:
        submit: Function that runs a request, typically `engine.submit`.
        loop: Event loop that runs `submit`; defaults to the running loop.

    Raises:
        RuntimeError: If `loop` is omitted and no event loop is running.
        TypeError: If `submit` is not callable.

    Example:
        ```python
        adapter = ExternalSchedulerAdapter(engine.submit)
        thread_scheduler.every(300, adapter.run_threadsafe)
        ```
    """

    def __init__(
        self,
        submit: Callable[[RefreshRequest], Awaitable[RefreshResult]],
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Create an adapter bound to an event loop."""
        if not callable(submit):
            raise TypeError("submit must be callable")
        self._submit = submit
        self._loop = loop if loop is not None else asyncio.get_running_loop()

    async def run(self, request: RefreshRequest | None = None) -> RefreshResult:
        """Run a request on the current event loop.

        Args:
            request: Request to run; defaults to an incremental refresh triggered by
                `TriggerSource.EXTERNAL`.

        Returns:
            The refresh result.
        """
        return await self._submit(self._request(request))

    def submit_threadsafe(
        self, request: RefreshRequest | None = None
    ) -> concurrent.futures.Future[RefreshResult]:
        """Schedule a request on the adapter's loop from any thread.

        Cancelling the returned future cancels the caller's wait for the refresh.

        Args:
            request: Request to run; defaults to an incremental refresh triggered by
                `TriggerSource.EXTERNAL`.

        Returns:
            A future that resolves to the refresh result.
        """
        return asyncio.run_coroutine_threadsafe(
            self._submit_coroutine(self._request(request)), self._loop
        )

    def run_threadsafe(
        self,
        request: RefreshRequest | None = None,
        *,
        timeout: float | None = None,
    ) -> RefreshResult:
        """Run a request from another thread and block until it finishes.

        Args:
            request: Request to run; defaults to an incremental refresh triggered by
                `TriggerSource.EXTERNAL`.
            timeout: Seconds to wait; on expiry the wait is cancelled.

        Returns:
            The refresh result.

        Raises:
            RuntimeError: If called from the adapter's event loop thread, which
                would deadlock.
            TimeoutError: If the result is not available within `timeout`.
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            raise RuntimeError(
                "run_threadsafe() would block the event loop; use run() instead"
            )
        future = self.submit_threadsafe(request)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    async def _submit_coroutine(self, request: RefreshRequest) -> RefreshResult:
        return await self._submit(request)

    @staticmethod
    def _request(request: RefreshRequest | None) -> RefreshRequest:
        if request is None:
            return RefreshRequest(trigger=TriggerSource.EXTERNAL)
        return request
