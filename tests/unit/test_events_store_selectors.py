from __future__ import annotations

import asyncio

import pytest
from structlog.testing import capture_logs

from refresh_engine import (
    EventKind,
    EventPublisher,
    IDSelector,
    InMemoryStateStore,
    PredicateSelector,
    Resource,
    ResourceFingerprint,
    ResourceState,
    TagSelector,
)
from refresh_engine.events import RefreshEvent
from refresh_engine.observability import (
    InMemoryMetrics,
    StructlogEventHandler,
    default_redactor,
)


@pytest.mark.asyncio
async def test_store_commit_and_rollback() -> None:
    store = InMemoryStateStore()
    state = ResourceState("a", ResourceFingerprint("test", "1"))
    async with store.transaction() as transaction:
        await transaction.put(state)
    assert (await store.load_all())["a"] == state
    with pytest.raises(RuntimeError):
        async with store.transaction() as transaction:
            await transaction.delete("a")
            raise RuntimeError("rollback")
    assert "a" in await store.load_all()


@pytest.mark.asyncio
async def test_event_publisher_orders_and_isolates_handlers() -> None:
    publisher = EventPublisher()
    seen: list[str] = []

    async def first(event: object) -> None:
        await asyncio.sleep(0)
        seen.append("first")

    def broken(event: object) -> None:
        raise RuntimeError("subscriber failed")

    def last(event: object) -> None:
        seen.append("last")

    publisher.subscribe(first)
    publisher.subscribe(broken)
    publisher.subscribe(last)
    await publisher.publish(RefreshEvent(EventKind.REFRESH_STARTED))
    await publisher.drain()
    assert seen == ["first", "last"]
    assert len(publisher.errors) == 1


def test_generic_selectors() -> None:
    resource = Resource("a", metadata={"tags": ["blue", "fast"]})
    assert IDSelector(["a"]).matches(resource)
    assert TagSelector(["blue"]).matches(resource)
    assert TagSelector(["blue", "missing"], match_all=False).matches(resource)
    assert PredicateSelector(lambda item: item.resource_id == "a").matches(resource)


def test_observability_hooks_redact_and_record() -> None:
    logger = StructlogEventHandler()
    with capture_logs() as logs:
        logger(
            RefreshEvent(
                EventKind.REFRESH_STARTED,
                "refresh-1",
                attributes={"token": "secret-value", "operation": "refresh"},
            )
        )
    assert logs == [
        {
            "event": "refresh_event",
            "event_kind": "refresh_started",
            "log_level": "info",
            "operation": "refresh",
            "refresh_id": "refresh-1",
            "resource_id": None,
            "timestamp": logs[0]["timestamp"],
            "token": "[REDACTED]",
        }
    ]
    assert default_redactor({"password": "value", "safe": "value"}) == {
        "password": "[REDACTED]",
        "safe": "value",
    }
    logger(object())

    metrics = InMemoryMetrics()
    metrics.increment("refreshes", attributes={"status": "success"})
    metrics.increment("refreshes", 2, {"status": "success"})
    metrics.observe("duration", 1.5)
    counters, observations = metrics.snapshot()
    assert counters["refreshes{status=success}"] == 3
    assert observations["duration"] == (1.5,)
