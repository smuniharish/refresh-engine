"""Refresh request overlap coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from refresh_engine.core.models import (
    OverlapPolicy,
    RefreshRequest,
    RefreshResult,
)

Runner = Callable[[RefreshRequest], Awaitable[RefreshResult]]


@dataclass(slots=True)
class _Pending:
    request: RefreshRequest
    futures: list[asyncio.Future[RefreshResult]] = field(default_factory=list)


class RefreshCoordinator:
    """Serialize refreshes and apply an explicit overlap policy."""

    def __init__(self, runner: Runner, policy: OverlapPolicy) -> None:
        self._runner = runner
        self._policy = policy
        self._lock = asyncio.Lock()
        self._pending: list[_Pending] = []
        self._worker: asyncio.Task[None] | None = None
        self._active: asyncio.Task[RefreshResult] | None = None
        self._active_pending: _Pending | None = None
        self._closed = False

    async def submit(self, request: RefreshRequest) -> RefreshResult:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[RefreshResult] = loop.create_future()
        async with self._lock:
            if self._closed:
                raise RuntimeError("refresh coordinator is closed")
            running = self._active is not None or bool(self._pending)
            if running and self._policy is OverlapPolicy.SKIP_IF_RUNNING:
                return RefreshResult.skipped("refresh already running")
            if running and self._policy is OverlapPolicy.CANCEL_PREVIOUS:
                if self._active is not None:
                    self._active.cancel()
                self._cancel_pending()
            if running and self._policy is OverlapPolicy.COALESCE and self._pending:
                pending = self._pending[-1]
                pending.request = pending.request.merge(request)
                pending.futures.append(future)
            else:
                self._pending.append(_Pending(request, [future]))
            if self._worker is None or self._worker.done():
                self._worker = asyncio.create_task(
                    self._work(), name="refresh-engine-coordinator"
                )
        try:
            return await future
        except asyncio.CancelledError:
            async with self._lock:
                for pending in self._pending:
                    if future in pending.futures:
                        pending.futures.remove(future)
                if (
                    self._active_pending is not None
                    and future in self._active_pending.futures
                    and all(item.done() for item in self._active_pending.futures)
                    and self._active is not None
                ):
                    self._active.cancel()
            raise

    async def close(self, *, cancel_active: bool) -> None:
        async with self._lock:
            self._closed = True
            if cancel_active and self._active is not None:
                self._active.cancel()
            if cancel_active:
                self._cancel_pending()
            worker = self._worker
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)

    async def _work(self) -> None:
        while True:
            async with self._lock:
                if not self._pending:
                    self._active = None
                    return
                pending = self._pending.pop(0)
                self._active = asyncio.create_task(
                    self._run_one(pending.request),
                    name="refresh-engine-active-refresh",
                )
                self._active_pending = pending
                active = self._active
            try:
                result = await active
            except asyncio.CancelledError:
                for future in pending.futures:
                    if not future.done():
                        future.cancel()
            except Exception as error:
                for future in pending.futures:
                    if not future.done():
                        future.set_exception(error)
            else:
                for future in pending.futures:
                    if not future.done():
                        future.set_result(result)
            finally:
                async with self._lock:
                    if self._active is active:
                        self._active = None
                        self._active_pending = None

    async def _run_one(self, request: RefreshRequest) -> RefreshResult:
        return await self._runner(request)

    def _cancel_pending(self) -> None:
        for pending in self._pending:
            for future in pending.futures:
                if not future.done():
                    future.cancel()
        self._pending.clear()
