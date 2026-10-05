"""Tests for the extension contracts in `refresh_engine.contracts`."""

from __future__ import annotations

import operator
import re
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, cast

import pytest

from refresh_engine import (
    DiscoveryResult,
    FingerprintStrategy,
    FingerprintStrategyABC,
    IDSelector,
    MetricsSink,
    MetricsSinkABC,
    PlanAction,
    RefreshOperation,
    RefreshOperationABC,
    RefreshRequest,
    Resource,
    ResourceFingerprint,
    ResourceSelector,
    ResourceSelectorABC,
    ResourceSnapshot,
    ResourceSource,
    ResourceSourceABC,
    ResourceState,
    StateStore,
    StateStoreABC,
    StateStoreError,
    StateTransaction,
    StateTransactionABC,
)
from refresh_engine.contracts import _Not, selector_union

ALPHA = Resource("alpha")
BETA = Resource("beta")
GAMMA = Resource("gamma")
DELTA = Resource("delta")
RESOURCES = (ALPHA, BETA, GAMMA, DELTA)
CLOSED_MESSAGE = f"^{re.escape('the state transaction is already closed')}$"


def state(resource_id: str, value: str = "v1") -> ResourceState:
    return ResourceState(
        resource_id,
        ResourceFingerprint("test", value),
        refreshed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def selected(selector: ResourceSelector) -> set[str]:
    return {
        resource.resource_id for resource in RESOURCES if selector.matches(resource)
    }


# Plain classes that satisfy the protocols without inheriting anything.


class PlainSource:
    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult([])

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(resource.resource_id)


class PlainOperation:
    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        return None


class PlainStrategy:
    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        return ResourceFingerprint("plain", snapshot.resource_id)


class PlainTransaction:
    async def put(self, state: ResourceState, /) -> None:
        return None

    async def delete(self, resource_id: str, /) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class PlainStore:
    async def load_all(self) -> Mapping[str, ResourceState]:
        return {}

    def transaction(self) -> StateTransaction:
        return PlainTransaction()


class PlainSelector:
    """A selector that satisfies the protocol but not the ABC."""

    def __init__(self, *resource_ids: str) -> None:
        self.resource_ids = frozenset(resource_ids)

    def matches(self, resource: Resource, /) -> bool:
        return resource.resource_id in self.resource_ids

    def __repr__(self) -> str:
        return f"PlainSelector{tuple(sorted(self.resource_ids))}"


class PlainSink:
    def increment(
        self,
        name: str,
        value: int = 1,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        return None

    def observe(
        self,
        name: str,
        value: float,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        return None


# Minimal subclasses of every ABC.


class SourceImpl(ResourceSourceABC):
    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult([])

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(resource.resource_id)


class OperationImpl(RefreshOperationABC):
    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        return None


class StrategyImpl(FingerprintStrategyABC):
    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        return ResourceFingerprint("impl", snapshot.resource_id)


class RecordingTransaction(StateTransactionABC):
    """Records every `apply_changes` call and optionally fails it."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self.error = error
        self.applied: list[tuple[Mapping[str, ResourceState], AbstractSet[str]]] = []

    async def apply_changes(
        self,
        puts: Mapping[str, ResourceState],
        deletes: AbstractSet[str],
        /,
    ) -> None:
        self.applied.append((puts, deletes))
        if self.error is not None:
            raise self.error


class StoreImpl(StateStoreABC):
    async def load_all(self) -> Mapping[str, ResourceState]:
        return {}

    def transaction(self) -> StateTransaction:
        return RecordingTransaction()


class SelectorImpl(ResourceSelectorABC):
    def matches(self, resource: Resource, /) -> bool:
        return True


class SinkImpl(MetricsSinkABC):
    def increment(
        self,
        name: str,
        value: int = 1,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        return None

    def observe(
        self,
        name: str,
        value: float,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        return None


PROTOCOL_IMPLEMENTATIONS: list[tuple[object, type]] = [
    (PlainSource(), ResourceSource),
    (PlainOperation(), RefreshOperation),
    (PlainStrategy(), FingerprintStrategy),
    (PlainTransaction(), StateTransaction),
    (PlainStore(), StateStore),
    (PlainSelector("alpha"), ResourceSelector),
    (PlainSink(), MetricsSink),
]

ABC_IMPLEMENTATIONS: list[tuple[object, type]] = [
    (SourceImpl(), ResourceSource),
    (OperationImpl(), RefreshOperation),
    (StrategyImpl(), FingerprintStrategy),
    (RecordingTransaction(), StateTransaction),
    (StoreImpl(), StateStore),
    (SelectorImpl(), ResourceSelector),
    (SinkImpl(), MetricsSink),
]


@pytest.mark.parametrize(
    ("implementation", "protocol"),
    PROTOCOL_IMPLEMENTATIONS,
    ids=[protocol.__name__ for _, protocol in PROTOCOL_IMPLEMENTATIONS],
)
def test_plain_classes_satisfy_runtime_checkable_protocols(
    implementation: object, protocol: type
) -> None:
    assert isinstance(implementation, protocol)


@pytest.mark.parametrize(
    "protocol",
    [protocol for _, protocol in PROTOCOL_IMPLEMENTATIONS],
    ids=lambda protocol: protocol.__name__,
)
def test_objects_without_the_methods_do_not_satisfy_protocols(protocol: type) -> None:
    assert not isinstance(object(), protocol)


def test_partial_implementations_do_not_satisfy_protocols() -> None:
    class DiscoverOnly:
        async def discover(self) -> DiscoveryResult:
            return DiscoveryResult([])

    assert not isinstance(DiscoverOnly(), ResourceSource)


@pytest.mark.parametrize(
    ("implementation", "protocol"),
    ABC_IMPLEMENTATIONS,
    ids=[type(implementation).__name__ for implementation, _ in ABC_IMPLEMENTATIONS],
)
def test_abc_subclasses_satisfy_the_matching_protocol(
    implementation: object, protocol: type
) -> None:
    assert isinstance(implementation, protocol)


@pytest.mark.parametrize(
    "abc",
    [
        ResourceSourceABC,
        RefreshOperationABC,
        FingerprintStrategyABC,
        StateTransactionABC,
        StateStoreABC,
        ResourceSelectorABC,
        MetricsSinkABC,
    ],
    ids=lambda abc: abc.__name__,
)
def test_abstract_base_classes_require_their_abstract_methods(abc: type) -> None:
    with pytest.raises(TypeError, match="abstract"):
        cast(Any, abc)()


def test_subclass_missing_an_abstract_method_cannot_be_instantiated() -> None:
    class DiscoverOnly(ResourceSourceABC):
        async def discover(self) -> DiscoveryResult:
            return DiscoveryResult([])

    with pytest.raises(TypeError, match="snapshot"):
        cast(Any, DiscoverOnly)()


# StateTransactionABC


def test_new_transaction_is_open() -> None:
    assert RecordingTransaction().closed is False


async def test_commit_passes_staged_puts_and_deletes_to_apply_changes() -> None:
    transaction = RecordingTransaction()
    alpha, beta = state("alpha"), state("beta")

    await transaction.put(alpha)
    await transaction.put(beta)
    await transaction.delete("gamma")
    await transaction.commit()

    ((puts, deletes),) = transaction.applied
    assert puts == {"alpha": alpha, "beta": beta}
    assert deletes == frozenset({"gamma"})
    assert isinstance(puts, MappingProxyType)
    assert isinstance(deletes, frozenset)
    assert transaction.closed is True


async def test_committed_puts_are_read_only() -> None:
    transaction = RecordingTransaction()
    await transaction.put(state("alpha"))
    await transaction.commit()

    ((puts, _),) = transaction.applied
    with pytest.raises(TypeError):
        cast(Any, puts)["beta"] = state("beta")


async def test_commit_applies_puts_without_deletes() -> None:
    transaction = RecordingTransaction()
    alpha = state("alpha")

    await transaction.put(alpha)
    await transaction.commit()

    assert transaction.applied == [({"alpha": alpha}, frozenset())]


async def test_commit_applies_deletes_without_puts() -> None:
    transaction = RecordingTransaction()

    await transaction.delete("alpha")
    await transaction.commit()

    assert transaction.applied == [({}, frozenset({"alpha"}))]


async def test_put_replaces_a_staged_delete_of_the_same_id() -> None:
    transaction = RecordingTransaction()
    alpha = state("alpha")

    await transaction.delete("alpha")
    await transaction.put(alpha)
    await transaction.commit()

    assert transaction.applied == [({"alpha": alpha}, frozenset())]


async def test_delete_replaces_a_staged_put_of_the_same_id() -> None:
    transaction = RecordingTransaction()

    await transaction.put(state("alpha"))
    await transaction.delete("alpha")
    await transaction.commit()

    assert transaction.applied == [({}, frozenset({"alpha"}))]


async def test_later_put_replaces_an_earlier_put_of_the_same_id() -> None:
    transaction = RecordingTransaction()
    newer = state("alpha", "v2")

    await transaction.put(state("alpha", "v1"))
    await transaction.put(newer)
    await transaction.commit()

    assert transaction.applied == [({"alpha": newer}, frozenset())]


async def test_commit_without_staged_changes_does_not_apply_anything() -> None:
    transaction = RecordingTransaction()

    await transaction.commit()

    assert transaction.applied == []
    assert transaction.closed is True


async def test_commit_closes_the_transaction_even_if_apply_changes_fails() -> None:
    failure = StateStoreError("disk full")
    transaction = RecordingTransaction(error=failure)
    await transaction.put(state("alpha"))

    with pytest.raises(StateStoreError) as caught:
        await transaction.commit()

    assert caught.value is failure
    assert transaction.closed is True
    with pytest.raises(StateStoreError, match=CLOSED_MESSAGE):
        await transaction.commit()
    assert len(transaction.applied) == 1


async def put_alpha(transaction: StateTransactionABC) -> None:
    await transaction.put(state("alpha"))


async def delete_alpha(transaction: StateTransactionABC) -> None:
    await transaction.delete("alpha")


async def commit(transaction: StateTransactionABC) -> None:
    await transaction.commit()


async def rollback(transaction: StateTransactionABC) -> None:
    await transaction.rollback()


@pytest.mark.parametrize("close", [commit, rollback], ids=["commit", "rollback"])
@pytest.mark.parametrize(
    "operation", [put_alpha, delete_alpha, commit], ids=["put", "delete", "commit"]
)
async def test_closed_transactions_reject_further_changes(
    close: Callable[[StateTransactionABC], Any],
    operation: Callable[[StateTransactionABC], Any],
) -> None:
    transaction = RecordingTransaction()
    await close(transaction)

    with pytest.raises(StateStoreError, match=CLOSED_MESSAGE):
        await operation(transaction)

    assert transaction.applied == []


async def test_rollback_discards_staged_changes_and_is_idempotent() -> None:
    transaction = RecordingTransaction()
    await transaction.put(state("alpha"))
    await transaction.delete("beta")

    await transaction.rollback()
    await transaction.rollback()

    assert transaction.closed is True
    assert transaction.applied == []


async def test_rollback_after_commit_is_allowed() -> None:
    transaction = RecordingTransaction()
    await transaction.put(state("alpha"))
    await transaction.commit()

    await transaction.rollback()

    assert transaction.closed is True
    assert len(transaction.applied) == 1


async def test_context_manager_commits_when_the_block_succeeds() -> None:
    transaction = RecordingTransaction()
    alpha = state("alpha")

    async with transaction as entered:
        assert entered is transaction
        await entered.put(alpha)

    assert transaction.applied == [({"alpha": alpha}, frozenset())]
    assert transaction.closed is True


async def test_context_manager_rolls_back_when_the_block_raises() -> None:
    transaction = RecordingTransaction()

    async def stage_then_fail() -> None:
        async with transaction:
            await transaction.put(state("alpha"))
            raise ValueError("block failed")

    with pytest.raises(ValueError, match="block failed"):
        await stage_then_fail()

    assert transaction.applied == []
    assert transaction.closed is True


async def test_context_manager_does_not_commit_twice() -> None:
    transaction = RecordingTransaction()

    async with transaction:
        await transaction.put(state("alpha"))
        await transaction.commit()

    assert len(transaction.applied) == 1


async def test_context_manager_does_not_commit_after_an_explicit_rollback() -> None:
    transaction = RecordingTransaction()

    async with transaction:
        await transaction.put(state("alpha"))
        await transaction.rollback()

    assert transaction.applied == []


async def test_context_manager_propagates_errors_after_an_explicit_rollback() -> None:
    transaction = RecordingTransaction()

    async def roll_back_then_fail() -> None:
        async with transaction:
            await transaction.rollback()
            raise KeyError("alpha")

    with pytest.raises(KeyError):
        await roll_back_then_fail()

    assert transaction.closed is True
    assert transaction.applied == []


# ResourceSelectorABC composition


def test_and_selects_resources_matched_by_both_selectors() -> None:
    combined = IDSelector(["alpha", "beta"]) & IDSelector(["beta", "gamma"])

    assert isinstance(combined, ResourceSelectorABC)
    assert selected(combined) == {"beta"}


def test_or_selects_resources_matched_by_either_selector() -> None:
    combined = IDSelector("alpha") | IDSelector("gamma")

    assert isinstance(combined, ResourceSelectorABC)
    assert selected(combined) == {"alpha", "gamma"}


def test_invert_selects_resources_not_matched() -> None:
    inverted = ~IDSelector(["alpha", "beta"])

    assert isinstance(inverted, ResourceSelectorABC)
    assert selected(inverted) == {"gamma", "delta"}


def test_reflected_and_accepts_a_protocol_selector_on_the_left() -> None:
    plain = PlainSelector("alpha", "beta")
    abc = IDSelector(["beta", "gamma"])

    combined = cast(Any, plain) & abc

    assert isinstance(combined, ResourceSelectorABC)
    assert selected(combined) == {"beta"}
    assert repr(combined) == f"_AllOf(selectors=({plain!r}, {abc!r}))"


def test_reflected_or_accepts_a_protocol_selector_on_the_left() -> None:
    plain = PlainSelector("alpha")
    abc = IDSelector("gamma")

    combined = cast(Any, plain) | abc

    assert isinstance(combined, ResourceSelectorABC)
    assert selected(combined) == {"alpha", "gamma"}
    assert repr(combined) == f"_AnyOf(selectors=({plain!r}, {abc!r}))"


def test_chained_or_flattens_into_one_selector() -> None:
    first, second, third = IDSelector("alpha"), IDSelector("beta"), IDSelector("gamma")

    combined = first | second | third

    assert selected(combined) == {"alpha", "beta", "gamma"}
    assert repr(combined) == f"_AnyOf(selectors=({first!r}, {second!r}, {third!r}))"


def test_chained_and_flattens_into_one_selector() -> None:
    first = IDSelector(["alpha", "beta", "gamma"])
    second = IDSelector(["beta", "gamma"])
    third = IDSelector(["gamma", "delta"])

    combined = first & second & third

    assert selected(combined) == {"gamma"}
    assert repr(combined) == f"_AllOf(selectors=({first!r}, {second!r}, {third!r}))"


def test_grouped_operands_flatten_in_operand_order() -> None:
    a, b, c, d = (IDSelector(resource.resource_id) for resource in RESOURCES)

    combined = (a | b) | (c | d)

    assert selected(combined) == {"alpha", "beta", "gamma", "delta"}
    assert repr(combined) == f"_AnyOf(selectors=({a!r}, {b!r}, {c!r}, {d!r}))"


def test_reflected_operators_flatten_the_right_operand() -> None:
    plain = PlainSelector("alpha", "beta", "gamma")
    first, second = IDSelector(["beta", "gamma"]), IDSelector(["gamma", "delta"])

    intersection = cast(Any, plain) & (first & second)
    union = cast(Any, plain) | (first | second)

    assert repr(intersection) == f"_AllOf(selectors=({plain!r}, {first!r}, {second!r}))"
    assert repr(union) == f"_AnyOf(selectors=({plain!r}, {first!r}, {second!r}))"
    assert selected(intersection) == {"gamma"}
    assert selected(union) == {"alpha", "beta", "gamma", "delta"}


def test_different_operators_do_not_flatten_into_each_other() -> None:
    a, b, c = IDSelector("alpha"), IDSelector("beta"), IDSelector(["alpha", "gamma"])

    combined = (a | b) & c

    assert (
        repr(combined) == f"_AllOf(selectors=(_AnyOf(selectors=({a!r}, {b!r})), {c!r}))"
    )
    assert selected(combined) == {"alpha"}


def test_double_negation_returns_the_original_abc_selector() -> None:
    selector = IDSelector("alpha")

    assert ~~selector is selector


def test_negating_a_negated_protocol_selector_returns_an_abc_wrapper() -> None:
    plain = PlainSelector("alpha")
    # Operators only negate ABC selectors, so a negation of a protocol-only
    # selector can only be built directly.
    negated = _Not(plain)

    restored = ~negated

    assert selected(negated) == {"beta", "gamma", "delta"}
    assert isinstance(restored, ResourceSelectorABC)
    assert selected(restored) == {"alpha"}
    assert selected(restored | IDSelector("beta")) == {"alpha", "beta"}


def test_composite_selectors_compare_by_their_parts() -> None:
    first = IDSelector("alpha") | IDSelector("beta")
    second = IDSelector("alpha") | IDSelector("beta")

    assert first == second
    assert hash(first) == hash(second)
    assert first != (IDSelector("alpha") | IDSelector("gamma"))


class NonCallableMatches:
    matches = "alpha"


@pytest.mark.parametrize(
    "combine",
    [
        lambda selector: selector & 1,
        lambda selector: 1 & selector,
        lambda selector: selector | "alpha",
        lambda selector: "alpha" | selector,
        lambda selector: selector & NonCallableMatches(),
        lambda selector: NonCallableMatches() | selector,
    ],
    ids=["and-int", "rand-int", "or-str", "ror-str", "and-object", "ror-object"],
)
def test_operators_reject_non_selector_operands(
    combine: Callable[[Any], object],
) -> None:
    with pytest.raises(TypeError, match="unsupported operand"):
        combine(IDSelector("alpha"))


@pytest.mark.parametrize("method", ["__and__", "__rand__", "__or__", "__ror__"])
def test_operator_methods_return_not_implemented_for_non_selectors(
    method: str,
) -> None:
    assert getattr(IDSelector("alpha"), method)(object()) is NotImplemented


@pytest.mark.parametrize("combine", [operator.and_, operator.or_])
def test_operators_accept_any_object_with_a_matches_method(
    combine: Callable[[Any, Any], object],
) -> None:
    combined = combine(IDSelector("alpha"), PlainSelector("alpha"))

    assert isinstance(combined, ResourceSelectorABC)
    assert selected(cast(ResourceSelector, combined)) == {"alpha"}


def test_selector_union_combines_protocol_selectors() -> None:
    union = selector_union(PlainSelector("alpha"), PlainSelector("delta"))

    assert isinstance(union, ResourceSelectorABC)
    assert selected(union) == {"alpha", "delta"}


def test_selector_union_flattens_existing_unions() -> None:
    a, b, c = IDSelector("alpha"), IDSelector("beta"), PlainSelector("gamma")

    union = selector_union(a | b, c)

    assert repr(union) == f"_AnyOf(selectors=({a!r}, {b!r}, {c!r}))"
    assert selected(union) == {"alpha", "beta", "gamma"}
