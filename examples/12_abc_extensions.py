"""Extending refresh-engine through its abstract base classes."""

import asyncio
from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    RefreshOperationABC,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
    ResourceSourceABC,
    ResourceState,
    StateStoreABC,
    StateTransactionABC,
)


class FeatureFlags(ResourceSourceABC):
    """Feature flags read from a remote configuration service."""

    def __init__(self) -> None:
        self.flags = {"dark-mode": True, "new-checkout": False}

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(Resource(name) for name in sorted(self.flags))

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        return ResourceSnapshot(
            resource.resource_id, content=self.flags[resource.resource_id]
        )


class EdgeCache(RefreshOperationABC):
    """Push each flag to an edge cache."""

    def __init__(self) -> None:
        self.cache: dict[str, object] = {}

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        if snapshot is None:
            self.cache.pop(resource.resource_id, None)
        else:
            self.cache[resource.resource_id] = snapshot.content


class AuditedStore(StateStoreABC):
    """Keep state in memory and count committed changes."""

    def __init__(self) -> None:
        self.states: dict[str, ResourceState] = {}
        self.committed = 0

    async def load_all(self) -> Mapping[str, ResourceState]:
        return dict(self.states)

    def transaction(self) -> StateTransactionABC:
        return AuditedTransaction(self)


class AuditedTransaction(StateTransactionABC):
    def __init__(self, store: AuditedStore) -> None:
        super().__init__()
        self.store = store

    async def apply_changes(
        self,
        puts: Mapping[str, ResourceState],
        deletes: AbstractSet[str],
        /,
    ) -> None:
        for resource_id in deletes:
            del self.store.states[resource_id]
        self.store.states.update(puts)
        self.store.committed += len(puts) + len(deletes)


async def main() -> None:
    flags, cache, store = FeatureFlags(), EdgeCache(), AuditedStore()
    async with RefreshEngine(flags, cache, store=store) as engine:
        await engine.refresh()
        flags.flags["new-checkout"] = True
        result = await engine.refresh()
    print(f"cache:     {cache.cache}")
    print(f"refreshed: {result.refreshed_count}, committed changes: {store.committed}")


if __name__ == "__main__":
    asyncio.run(main())
