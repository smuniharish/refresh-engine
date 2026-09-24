"""Transactional in-memory state store."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import MappingProxyType

from refresh_engine.api.abc import StateStoreABC, StateTransactionABC
from refresh_engine.core.models import ResourceState
from refresh_engine.errors import StateStoreError


class InMemoryStateTransaction(StateTransactionABC):
    def __init__(self, store: InMemoryStateStore) -> None:
        self._store = store
        self._puts: dict[str, ResourceState] = {}
        self._deletes: set[str] = set()
        self._closed = False

    async def put(self, state: ResourceState) -> None:
        self._ensure_open()
        self._puts[state.resource_id] = state
        self._deletes.discard(state.resource_id)

    async def delete(self, resource_id: str) -> None:
        self._ensure_open()
        self._deletes.add(resource_id)
        self._puts.pop(resource_id, None)

    async def commit(self) -> None:
        self._ensure_open()
        async with self._store._lock:
            updated = dict(self._store._states)
            for resource_id in self._deletes:
                updated.pop(resource_id, None)
            updated.update(self._puts)
            self._store._states = updated
        self._closed = True

    async def rollback(self) -> None:
        self._puts.clear()
        self._deletes.clear()
        self._closed = True

    async def __aenter__(self) -> InMemoryStateTransaction:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        if exc_type is not None and not self._closed:
            await self.rollback()
        elif not self._closed:
            await self.commit()

    def _ensure_open(self) -> None:
        if self._closed:
            raise StateStoreError("state transaction is already closed")


class InMemoryStateStore(StateStoreABC):
    """Copy-on-write state with serialized atomic commits."""

    def __init__(self, initial: Mapping[str, ResourceState] | None = None) -> None:
        self._states = dict(initial or {})
        self._lock = asyncio.Lock()

    async def load_all(self) -> Mapping[str, ResourceState]:
        async with self._lock:
            return MappingProxyType(dict(self._states))

    def transaction(self) -> InMemoryStateTransaction:
        return InMemoryStateTransaction(self)
