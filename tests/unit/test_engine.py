from __future__ import annotations

from typing import Any, cast

import pytest

from refresh_engine import (
    CompositeHash,
    ConfigurationError,
    ContentHash,
    EventPublisher,
    InMemoryStateStore,
    RefreshConfig,
    RefreshEngine,
    RefreshMode,
    TagSelector,
    TriggerSource,
)
from tests.support import FakeSource, RecordingOperation


def test_engine_uses_documented_defaults() -> None:
    source = FakeSource({})
    operation = RecordingOperation()
    engine = RefreshEngine(source, operation)
    assert engine.source is source
    assert engine.operation is operation
    assert isinstance(engine.fingerprint, CompositeHash)
    assert isinstance(engine.store, InMemoryStateStore)
    assert engine.config == RefreshConfig()
    assert isinstance(engine.events, EventPublisher)
    assert not engine.closed


def test_engine_keeps_injected_components_even_when_falsy() -> None:
    class EmptyStore(InMemoryStateStore):
        def __len__(self) -> int:
            return 0

    store = EmptyStore()
    fingerprint = ContentHash()
    config = RefreshConfig(max_concurrency=2)
    events = EventPublisher()
    engine = RefreshEngine(
        FakeSource({}),
        RecordingOperation(),
        fingerprint=fingerprint,
        store=store,
        config=config,
        events=events,
    )
    assert not store
    assert engine.store is store
    assert engine.fingerprint is fingerprint
    assert engine.config is config
    assert engine.events is events


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"source": object()}, "source must provide discover"),
        ({"operation": object()}, "operation must be callable"),
        ({"fingerprint": object()}, "fingerprint must provide fingerprint"),
        ({"store": object()}, "store must provide load_all"),
        ({"config": object()}, "config must be a RefreshConfig"),
        ({"events": object()}, "events must be an EventPublisherABC"),
    ],
)
def test_engine_rejects_components_that_violate_their_contract(
    arguments: dict[str, object], message: str
) -> None:
    values: dict[str, Any] = {
        "source": FakeSource({}),
        "operation": RecordingOperation(),
        **arguments,
    }
    source = values.pop("source")
    operation = values.pop("operation")
    with pytest.raises(TypeError, match=message):
        RefreshEngine(source, operation, **values)


async def test_refresh_infers_the_mode_from_its_arguments() -> None:
    operation = RecordingOperation()
    async with RefreshEngine(FakeSource({"a": 1}), operation) as engine:
        incremental = await engine.refresh()
        by_id = await engine.refresh(resource_ids=("a",))
        by_selector = await engine.refresh(selector=TagSelector("x"))
        full = await engine.refresh(mode=RefreshMode.FULL)
    assert incremental.request.mode is RefreshMode.INCREMENTAL
    assert by_id.request.mode is RefreshMode.TARGETED
    assert by_selector.request.mode is RefreshMode.TARGETED
    assert full.request.mode is RefreshMode.FULL


async def test_refresh_builds_the_request_from_its_arguments() -> None:
    async with RefreshEngine(FakeSource({}), RecordingOperation()) as engine:
        result = await engine.refresh(
            trigger=TriggerSource.EVENT,
            metadata={"reason": "webhook"},
            correlation_id="abc",
        )
    request = result.request
    assert request.trigger is TriggerSource.EVENT
    assert request.metadata == {"reason": "webhook"}
    assert request.correlation_id == "abc"


async def test_refresh_rejects_inconsistent_arguments() -> None:
    async with RefreshEngine(FakeSource({}), RecordingOperation()) as engine:
        with pytest.raises(ConfigurationError, match="only valid for targeted"):
            await engine.refresh(mode=RefreshMode.FULL, resource_ids=["a"])
        with pytest.raises(ConfigurationError, match="non-empty strings"):
            await engine.refresh(resource_ids=cast(Any, [1]))
