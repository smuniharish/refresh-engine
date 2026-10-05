"""Serialization of refresh requests under an overlap policy."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Coroutine
from contextvars import Context, copy_context
from dataclasses import dataclass, field
from typing import Any, Final

from refresh_engine._internal.context import RunTicket
from refresh_engine._internal.reentrancy import awaited_by, bind
from refresh_engine.contracts import ResourceSelector, selector_union
from refresh_engine.errors import EngineClosedError
from refresh_engine.models import (
    OverlapPolicy,
    RefreshMode,
    RefreshRequest,
    RefreshResult,
    RefreshStatus,
)

type Runner = Callable[[RunTicket], Coroutine[Any, Any, RefreshResult]]
type Finisher = Callable[[RunTicket, RefreshStatus, str], RefreshResult]

_COVERS: Final[dict[RefreshMode, frozenset[RefreshMode]]] = {
    RefreshMode.FULL: frozenset(RefreshMode),
    RefreshMode.DEPENDENCY_AWARE: frozenset(
        {RefreshMode.DEPENDENCY_AWARE, RefreshMode.INCREMENTAL}
    ),
    RefreshMode.INCREMENTAL: frozenset({RefreshMode.INCREMENTAL}),
    RefreshMode.TARGETED: frozenset({RefreshMode.TARGETED}),
}

SUPERSEDED: Final = "the refresh was superseded by a newer request"
SHUTDOWN: Final = "the refresh was cancelled because the engine is closing"
ABANDONED: Final = "the refresh was cancelled because every caller stopped waiting"
BUSY: Final = "the request was skipped because another refresh is running"
REENTRANT: Final = (
    "a refresh of this engine cannot be awaited by its own source, operation, "
    "fingerprint strategy, or state store, because it would wait for itself; "
    "request it after the refresh returns, or in a separate task that the refresh "
    "does not wait for"
)


def coalesce(first: RefreshRequest, second: RefreshRequest) -> RefreshRequest | None:
    """Merge two requests into one that does the work of both, if possible.

    The merged request keeps the earliest request time and the first request's
    trigger and correlation ID (falling back to the second's). Metadata is merged,
    with the second request's values taking precedence.
    """
    if second.mode in _COVERS[first.mode]:
        mode = first.mode
    elif first.mode in _COVERS[second.mode]:
        mode = second.mode
    else:
        return None
    resource_ids: frozenset[str] = frozenset()
    selector: ResourceSelector | None = None
    if mode is RefreshMode.TARGETED:
        resource_ids = frozenset(first.resource_ids | second.resource_ids)
        if first.selector is None or first.selector == second.selector:
            selector = second.selector
        elif second.selector is None:
            selector = first.selector
        else:
            selector = selector_union(first.selector, second.selector)
    return RefreshRequest(
        mode=mode,
        resource_ids=resource_ids,
        selector=selector,
        trigger=first.trigger,
        metadata={**first.metadata, **second.metadata},
        correlation_id=first.correlation_id or second.correlation_id,
        requested_at=min(first.requested_at, second.requested_at),
    )


@dataclass(slots=True, eq=False)
class _Entry:
    ticket: RunTicket
    waiters: list[asyncio.Future[RefreshResult]] = field(default_factory=list)
    cancel_reason: str = SUPERSEDED
    context: Context = field(default_factory=copy_context)


class RefreshCoordinator:
    """Run one refresh at a time and apply an overlap policy to new requests."""

    def __init__(self, run: Runner, finish: Finisher, policy: OverlapPolicy) -> None:
        self._run = run
        self._finish = finish
        self._policy = policy
        self._pending: deque[_Entry] = deque()
        self._active: _Entry | None = None
        self._active_task: asyncio.Task[RefreshResult] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether new requests are rejected."""
        return self._closed

    async def submit(self, request: RefreshRequest) -> RefreshResult:
        """Run `request` according to the overlap policy and return its result.

        A request runs in a copy of the context of the caller that queued it.

        Raises:
            EngineClosedError: If the coordinator is closed.
            RuntimeError: If called from a task that a refresh of this coordinator
                waits for, where waiting could never finish.
        """
        if self._closed:
            raise EngineClosedError("the refresh engine is closed")
        if awaited_by(self):
            raise RuntimeError(REENTRANT)
        busy = self._active is not None or bool(self._pending)
        if busy and self._policy is OverlapPolicy.SKIP_IF_RUNNING:
            return self._finish(RunTicket(request), RefreshStatus.SKIPPED, BUSY)
        if busy and self._policy is OverlapPolicy.CANCEL_PREVIOUS:
            self._cancel_all(SUPERSEDED)
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[RefreshResult] = loop.create_future()
        if self._policy is not OverlapPolicy.COALESCE or not self._absorb(
            request, waiter
        ):
            self._pending.append(_Entry(RunTicket(request), [waiter]))
        if self._worker is None or self._worker.done():
            self._worker = loop.create_task(
                self._work(), name="refresh-engine-coordinator"
            )
        try:
            return await waiter
        except asyncio.CancelledError:
            self._abandon(waiter)
            raise

    async def close(self, *, cancel: bool, wait: bool = True) -> None:
        """Reject new requests and optionally wait until accepted requests finish.

        Args:
            cancel: Cancel the running and queued requests instead of letting them
                finish.
            wait: Wait until the accepted requests have finished.
        """
        self._closed = True
        if cancel:
            self._cancel_all(SHUTDOWN)
        worker = self._worker
        if wait and worker is not None and not worker.done():
            await asyncio.wait((worker,))

    def _absorb(
        self, request: RefreshRequest, waiter: asyncio.Future[RefreshResult]
    ) -> bool:
        for entry in self._pending:
            merged = coalesce(entry.ticket.request, request)
            if merged is not None:
                entry.ticket.request = merged
                entry.waiters.append(waiter)
                return True
        return False

    def _abandon(self, waiter: asyncio.Future[RefreshResult]) -> None:
        for entry in self._pending:
            if waiter in entry.waiters:
                entry.waiters.remove(waiter)
                if not entry.waiters:
                    self._pending.remove(entry)
                return
        active = self._active
        if active is not None and waiter in active.waiters:
            active.waiters.remove(waiter)
            if not active.waiters:
                self._cancel_active(ABANDONED)

    def _cancel_active(self, reason: str) -> None:
        active, task = self._active, self._active_task
        if active is not None and task is not None and not task.done():
            active.cancel_reason = reason
            task.cancel(reason)

    def _cancel_all(self, reason: str) -> None:
        self._cancel_active(reason)
        while self._pending:
            entry = self._pending.popleft()
            self._resolve(
                entry, self._finish(entry.ticket, RefreshStatus.CANCELLED, reason)
            )

    async def _work(self) -> None:
        loop = asyncio.get_running_loop()
        while self._pending:
            entry = self._pending.popleft()
            task = loop.create_task(
                self._execute(entry.ticket),
                name="refresh-engine-refresh",
                context=entry.context,
            )
            self._active = entry
            self._active_task = task
            try:
                await asyncio.wait((task,))
            finally:
                self._active = None
                self._active_task = None
            if task.cancelled():
                result = entry.ticket.cancelled_result or self._finish(
                    entry.ticket, RefreshStatus.CANCELLED, entry.cancel_reason
                )
                self._resolve(entry, result)
                continue
            error = task.exception()
            if error is not None:
                self._reject(entry, error)
                continue
            self._resolve(entry, task.result())

    async def _execute(self, ticket: RunTicket) -> RefreshResult:
        bind(self)
        return await self._run(ticket)

    @staticmethod
    def _resolve(entry: _Entry, result: RefreshResult) -> None:
        for waiter in entry.waiters:
            if not waiter.done():
                waiter.set_result(result)

    @staticmethod
    def _reject(entry: _Entry, error: BaseException) -> None:
        for waiter in entry.waiters:
            if not waiter.done():
                waiter.set_exception(error)
