"""Built-in state store."""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from types import MappingProxyType

from refresh_engine.contracts import StateStoreABC, StateTransactionABC
from refresh_engine.models import ResourceState

__all__ = ["InMemoryStateStore"]


class InMemoryStateStore(StateStoreABC):
    """Process-local state store; the default store of every engine.

    Commits replace the stored mapping atomically (copy-on-write), so
    [`load_all`][refresh_engine.InMemoryStateStore.load_all] returns an immutable
    snapshot in constant time. State is lost when the process exits; inject a
    durable store to keep state across restarts.

    Args:
        initial: States to start with, keyed by resource ID.
    """

    def __init__(self, initial: Mapping[str, ResourceState] | None = None) -> None:
        """Create a store holding `initial`."""
        self._states: Mapping[str, ResourceState] = MappingProxyType(
            dict(initial or {})
        )

    async def load_all(self) -> Mapping[str, ResourceState]:
        """Return an immutable snapshot of every stored state."""
        return self._states

    def transaction(self) -> StateTransactionABC:
        """Create a transaction that applies its changes atomically on commit."""
        return _InMemoryTransaction(self)

    def _apply(
        self, puts: Mapping[str, ResourceState], deletes: AbstractSet[str]
    ) -> None:
        states = dict(self._states)
        for resource_id in deletes:
            states.pop(resource_id, None)
        states.update(puts)
        self._states = MappingProxyType(states)


class _InMemoryTransaction(StateTransactionABC):
    def __init__(self, store: InMemoryStateStore) -> None:
        super().__init__()
        self._store = store

    async def apply_changes(
        self,
        puts: Mapping[str, ResourceState],
        deletes: AbstractSet[str],
        /,
    ) -> None:
        self._store._apply(puts, deletes)
