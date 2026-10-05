"""Tests for the built-in `InMemoryStateStore`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from refresh_engine import (
    InMemoryStateStore,
    ResourceFingerprint,
    ResourceState,
    StateStore,
    StateStoreABC,
    StateStoreError,
    StateTransaction,
    StateTransactionABC,
)


def state(resource_id: str, value: str = "v1") -> ResourceState:
    return ResourceState(
        resource_id,
        ResourceFingerprint("test", value),
        refreshed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


ALPHA = state("alpha")
BETA = state("beta")
GAMMA = state("gamma")


def test_store_implements_both_store_contracts() -> None:
    store = InMemoryStateStore()

    assert isinstance(store, StateStoreABC)
    assert isinstance(store, StateStore)


def test_transactions_implement_both_transaction_contracts() -> None:
    transaction = InMemoryStateStore().transaction()

    assert isinstance(transaction, StateTransactionABC)
    assert isinstance(transaction, StateTransaction)
    assert transaction.closed is False


def test_every_transaction_is_a_new_object() -> None:
    store = InMemoryStateStore()

    assert store.transaction() is not store.transaction()


async def test_new_store_is_empty() -> None:
    assert await InMemoryStateStore().load_all() == {}


async def test_store_starts_with_the_initial_states() -> None:
    store = InMemoryStateStore({"alpha": ALPHA, "beta": BETA})

    assert await store.load_all() == {"alpha": ALPHA, "beta": BETA}


async def test_store_copies_the_initial_states() -> None:
    initial = {"alpha": ALPHA}
    store = InMemoryStateStore(initial)

    initial["beta"] = BETA

    assert await store.load_all() == {"alpha": ALPHA}


async def test_load_all_returns_a_read_only_mapping() -> None:
    states = await InMemoryStateStore({"alpha": ALPHA}).load_all()

    with pytest.raises(TypeError):
        cast(Any, states)["beta"] = BETA


async def test_loaded_snapshot_does_not_change_after_later_commits() -> None:
    store = InMemoryStateStore({"alpha": ALPHA})
    before = await store.load_all()

    transaction = store.transaction()
    await transaction.put(BETA)
    await transaction.delete("alpha")
    await transaction.commit()

    assert before == {"alpha": ALPHA}
    assert await store.load_all() == {"beta": BETA}


async def test_commit_applies_puts_and_deletes_together() -> None:
    store = InMemoryStateStore({"alpha": ALPHA, "beta": BETA})
    updated = state("alpha", "v2")
    transaction = store.transaction()

    await transaction.put(updated)
    await transaction.put(GAMMA)
    await transaction.delete("beta")

    assert await store.load_all() == {"alpha": ALPHA, "beta": BETA}
    await transaction.commit()
    assert await store.load_all() == {"alpha": updated, "gamma": GAMMA}


async def test_deleting_unknown_ids_is_ignored() -> None:
    store = InMemoryStateStore({"alpha": ALPHA})
    transaction = store.transaction()

    await transaction.delete("missing")
    await transaction.commit()

    assert await store.load_all() == {"alpha": ALPHA}


async def test_rollback_leaves_the_state_untouched() -> None:
    store = InMemoryStateStore({"alpha": ALPHA})
    transaction = store.transaction()
    await transaction.put(BETA)
    await transaction.delete("alpha")

    await transaction.rollback()

    assert await store.load_all() == {"alpha": ALPHA}
    assert transaction.closed is True


async def test_transaction_rejects_changes_after_commit() -> None:
    transaction = InMemoryStateStore().transaction()
    await transaction.commit()

    with pytest.raises(StateStoreError, match="already closed"):
        await transaction.put(ALPHA)


async def test_transaction_context_manager_commits_on_success() -> None:
    store = InMemoryStateStore()

    async with store.transaction() as transaction:
        await transaction.put(ALPHA)

    assert await store.load_all() == {"alpha": ALPHA}


async def test_transaction_context_manager_rolls_back_on_error() -> None:
    store = InMemoryStateStore({"alpha": ALPHA})

    async def delete_then_fail() -> None:
        async with store.transaction() as transaction:
            await transaction.delete("alpha")
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        await delete_then_fail()

    assert await store.load_all() == {"alpha": ALPHA}


async def test_interleaved_transactions_keep_each_others_changes() -> None:
    store = InMemoryStateStore()
    first = store.transaction()
    second = store.transaction()
    await first.put(ALPHA)
    await second.put(BETA)

    await first.commit()
    await second.commit()

    assert await store.load_all() == {"alpha": ALPHA, "beta": BETA}
