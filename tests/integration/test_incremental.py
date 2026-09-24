from __future__ import annotations

import pytest

from refresh_engine import (
    RefreshEngine,
    RefreshMode,
    RefreshStatus,
)
from tests.helpers import MutableSource, RecordingOperation


@pytest.mark.asyncio
async def test_ten_thousand_unchanged_resources_are_skipped() -> None:
    source = MutableSource({str(index): index for index in range(10_000)})
    operation = RecordingOperation()
    engine = RefreshEngine(source, operation)
    first = await engine.refresh()
    calls_after_first = len(operation.calls)
    second = await engine.refresh()
    assert first.refreshed_count == 10_000
    assert second.unchanged_count == 10_000
    assert second.skipped_count == 10_000
    assert second.refreshed_count == 0
    assert len(operation.calls) == calls_after_first
    await engine.close()


@pytest.mark.asyncio
async def test_only_one_changed_resource_is_refreshed() -> None:
    source = MutableSource({str(index): index for index in range(10_000)})
    operation = RecordingOperation()
    engine = RefreshEngine(source, operation)
    await engine.refresh()
    operation.calls.clear()
    source.values["5000"] = "changed"
    result = await engine.refresh()
    assert result.modified_count == 1
    assert result.unchanged_count == 9_999
    assert result.refreshed_count == 1
    assert operation.calls == [("5000", operation.calls[0][1])]
    await engine.close()


@pytest.mark.asyncio
async def test_added_and_deleted_resources() -> None:
    source = MutableSource({"a": 1})
    operation = RecordingOperation()
    engine = RefreshEngine(source, operation)
    await engine.refresh()
    source.values["b"] = 2
    added = await engine.refresh()
    assert added.added_count == 1
    assert added.refreshed_count == 1
    del source.values["a"]
    deleted = await engine.refresh()
    assert deleted.deleted_count == 1
    assert deleted.refreshed_count == 1
    await engine.close()


@pytest.mark.asyncio
async def test_dependency_aware_refresh_uses_dependency_order() -> None:
    source = MutableSource(
        {"A": 1, "B": 1, "C": 1},
        dependencies={"A": {"B"}, "B": {"C"}},
    )
    operation = RecordingOperation()
    engine = RefreshEngine(source, operation)
    await engine.refresh()
    operation.calls.clear()
    source.values["C"] = 2
    result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
    assert [resource_id for resource_id, _ in operation.calls] == ["C", "B", "A"]
    assert result.impacted_count == 2
    await engine.close()


@pytest.mark.asyncio
async def test_failed_refresh_does_not_advance_fingerprint() -> None:
    source = MutableSource({"a": "A"})
    operation = RecordingOperation()
    engine = RefreshEngine(source, operation)
    await engine.refresh()
    old = (await engine.store.load_all())["a"].fingerprint
    source.values["a"] = "B"
    operation.failures.add("a")
    failed = await engine.refresh()
    assert failed.status in (RefreshStatus.PARTIAL, RefreshStatus.FAILED)
    assert (await engine.store.load_all())["a"].fingerprint == old
    operation.failures.clear()
    retry = await engine.refresh()
    assert retry.modified_count == 1
    assert retry.refreshed_count == 1
    await engine.close()


@pytest.mark.asyncio
async def test_full_and_targeted_modes_select_expected_resources() -> None:
    source = MutableSource({"a": 1, "b": 2})
    operation = RecordingOperation()
    engine = RefreshEngine(source, operation)
    await engine.refresh()
    operation.calls.clear()
    full = await engine.refresh(mode=RefreshMode.FULL)
    assert full.refreshed_count == 2
    operation.calls.clear()
    targeted = await engine.refresh(mode=RefreshMode.TARGETED, resource_ids={"b"})
    assert targeted.refreshed_count == 1
    assert operation.calls[0][0] == "b"
    await engine.close()
