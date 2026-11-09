"""Reusable conformance tests for state-store implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from refresh_engine.contracts import StateStore
from refresh_engine.models import ResourceFingerprint, ResourceState

__all__ = ["StateStoreContract"]


class StateStoreContract(ABC):
    """Async test suite for a custom state store.

    Subclass this in a test module and implement :meth:`make_store`. Pytest
    collects the inherited ``test_*`` methods when the subclass name starts with
    ``Test``.
    """

    @abstractmethod
    async def make_store(self) -> StateStore:
        """Return a fresh store for one test."""

    async def test_store_starts_empty(self) -> None:
        """Check that the initial store has no records."""
        store = await self.make_store()
        assert await store.load_all() == {}

    async def test_commit_insert_and_overwrite(self) -> None:
        """Check insertion and replacement through committed transactions."""
        store = await self.make_store()
        transaction = await store.transaction()
        await transaction.put(_state("alpha", "first"))
        await transaction.commit()
        assert transaction.closed
        assert (await store.load_all())["alpha"].fingerprint.value == "first"

        transaction = await store.transaction()
        await transaction.put(_state("alpha", "second"))
        await transaction.commit()
        assert (await store.load_all())["alpha"].fingerprint.value == "second"

    async def test_delete_and_rollback(self) -> None:
        """Check deletion commits and rollback preserves existing records."""
        store = await self.make_store()
        transaction = await store.transaction()
        await transaction.put(_state("alpha", "first"))
        await transaction.commit()

        transaction = await store.transaction()
        await transaction.delete("alpha")
        await transaction.rollback()
        assert transaction.closed
        assert "alpha" in await store.load_all()

        transaction = await store.transaction()
        await transaction.delete("alpha")
        await transaction.commit()
        assert "alpha" not in await store.load_all()

    async def test_context_manager_commits_and_rolls_back(self) -> None:
        """Check context-manager commit and rollback behavior."""
        store = await self.make_store()
        async with await store.transaction() as transaction:
            await transaction.put(_state("committed", "value"))
        assert "committed" in await store.load_all()

        propagated = False
        try:
            async with await store.transaction() as transaction:
                await transaction.put(_state("rolled-back", "value"))
                raise _RollbackError
        except _RollbackError:
            propagated = True
        assert propagated, "the transaction body exception should be propagated"
        assert "rolled-back" not in await store.load_all()

    async def test_loaded_snapshot_is_immutable(self) -> None:
        """Check that loaded state cannot be mutated by callers."""
        store = await self.make_store()
        transaction = await store.transaction()
        await transaction.put(_state("alpha", "value"))
        await transaction.commit()
        snapshot = await store.load_all()
        immutable = False
        try:
            snapshot["other"] = _state("other", "value")  # type: ignore[index]
        except TypeError:
            immutable = True
        assert immutable, "load_all() must return an immutable snapshot"


def _state(resource_id: str, value: str) -> ResourceState:
    return ResourceState(
        resource_id,
        ResourceFingerprint("state-store-contract", value),
        refreshed_at=datetime.now(UTC),
    )


class _RollbackError(Exception):
    pass
