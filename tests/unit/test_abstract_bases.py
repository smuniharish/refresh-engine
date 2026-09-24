from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from refresh_engine import (
    AsyncScheduler,
    AsyncSchedulerABC,
    CompositeHash,
    DiscoveryResult,
    EventPublisher,
    EventPublisherABC,
    FingerprintStrategyABC,
    IDSelector,
    InMemoryStateStore,
    MetricsSinkABC,
    PlanAction,
    RefreshOperationABC,
    RefreshRequest,
    Resource,
    ResourceSelectorABC,
    ResourceSnapshot,
    ResourceSourceABC,
    StateStoreABC,
    StateTransactionABC,
)


class Source(ResourceSourceABC):
    async def discover(self) -> DiscoveryResult:
        async def resources() -> AsyncIterator[Resource]:
            yield Resource("a")

        return DiscoveryResult(resources())

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        return ResourceSnapshot(resource.resource_id, content="value")


class Operation(RefreshOperationABC):
    async def __call__(
        self,
        resource: Resource | None,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
    ) -> None:
        return None


def test_complete_abstract_extensions_can_be_instantiated() -> None:
    assert isinstance(Source(), ResourceSourceABC)
    assert isinstance(Operation(), RefreshOperationABC)
    assert isinstance(CompositeHash(), FingerprintStrategyABC)
    assert isinstance(IDSelector(["a"]), ResourceSelectorABC)
    assert isinstance(InMemoryStateStore(), StateStoreABC)
    assert isinstance(EventPublisher(), EventPublisherABC)


def test_incomplete_abstract_extensions_cannot_be_instantiated() -> None:
    def instantiate(class_: type[object]) -> object:
        return class_()

    abstract_classes: tuple[type[object], ...] = (
        ResourceSourceABC,
        FingerprintStrategyABC,
        RefreshOperationABC,
        StateTransactionABC,
        StateStoreABC,
        ResourceSelectorABC,
        MetricsSinkABC,
        AsyncSchedulerABC,
    )
    for abstract_class in abstract_classes:
        with pytest.raises(TypeError):
            instantiate(abstract_class)


def test_builtin_scheduler_implements_lifecycle_abc() -> None:
    async def callback() -> None:
        return None

    assert isinstance(AsyncScheduler(callback, 1), AsyncSchedulerABC)
