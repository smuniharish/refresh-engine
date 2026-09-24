"""Adapter for externally owned schedulers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from refresh_engine.core.models import RefreshRequest, RefreshResult


class ExternalSchedulerAdapter:
    def __init__(
        self, submit: Callable[[RefreshRequest], Awaitable[RefreshResult]]
    ) -> None:
        self._submit = submit

    async def run(self, request: RefreshRequest) -> RefreshResult:
        return await self._submit(request)
