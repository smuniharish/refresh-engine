from __future__ import annotations

import asyncio

import pytest

from refresh_engine import (
    ContentHash,
    InMemoryStateStore,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshMode,
    RefreshStatus,
    Resource,
    ResourceSnapshot,
    TagSelector,
)
from tests.support import FakeSource, RecordingOperation

REFRESH = PlanAction.REFRESH
DELETE = PlanAction.DELETE


async def test_first_refresh_adds_and_refreshes_every_resource() -> None:
    operation = RecordingOperation()
    async with RefreshEngine(FakeSource({"a": 1, "b": 2, "c": 3}), operation) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.SUCCESS
    assert result.request.mode is RefreshMode.INCREMENTAL
    assert (result.discovered_count, result.added_count) == (3, 3)
    assert (result.refreshed_count, result.skipped_count, result.failed_count) == (
        3,
        0,
        0,
    )
    assert sorted(operation.ids()) == ["a", "b", "c"]
    assert [execution.attempts for execution in result.executions] == [1, 1, 1]
    assert all(execution.succeeded for execution in result.executions)
    assert result.errors == ()
    assert result.warnings == ()
    assert result.completed_at >= result.started_at
    assert result.duration >= 0


async def test_unchanged_resources_are_skipped() -> None:
    source = FakeSource({"a": 1, "b": 2})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        result = await engine.refresh()
    assert result.status is RefreshStatus.SUCCESS
    assert (result.unchanged_count, result.skipped_count, result.refreshed_count) == (
        2,
        2,
        0,
    )
    assert operation.calls == []
    assert result.executions == ()


async def test_only_the_changed_resource_is_refreshed_among_ten_thousand() -> None:
    source = FakeSource({str(index): index for index in range(10_000)}, cheap=True)
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        first = await engine.refresh()
        operation.calls.clear()
        source.snapshot_calls.clear()
        source.values["5000"] = "changed"
        second = await engine.refresh()
    assert first.refreshed_count == 10_000
    assert (second.modified_count, second.unchanged_count) == (1, 9_999)
    assert second.refreshed_count == 1
    assert operation.calls == [(REFRESH, "5000")]
    assert source.snapshot_calls == ["5000"]


async def test_reliable_cheap_indicator_skips_snapshots_of_unchanged_resources() -> (
    None
):
    source = FakeSource({"a": 1, "b": 2}, cheap=True)
    async with RefreshEngine(source, RecordingOperation()) as engine:
        await engine.refresh()
        source.snapshot_calls.clear()
        result = await engine.refresh()
    assert source.snapshot_calls == []
    assert result.unchanged_count == 2


async def test_unreliable_resources_are_always_snapshotted() -> None:
    source = FakeSource({"a": 1})
    async with RefreshEngine(source, RecordingOperation()) as engine:
        await engine.refresh()
        source.snapshot_calls.clear()
        await engine.refresh()
    assert source.snapshot_calls == ["a"]


async def test_cheap_match_keeps_dependencies_declared_only_by_snapshots() -> None:
    source = FakeSource(
        {"base": 1, "derived": 1},
        dependencies={"derived": {"base"}},
        dependencies_in_snapshot=True,
        cheap=True,
    )
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        result = await engine.refresh()
        state = (await engine.store.load_all())["derived"]
    assert result.unchanged_count == 2
    assert result.dependency_changed_count == 0
    assert operation.calls == []
    assert state.dependencies == {"base"}


async def test_dependency_change_declared_by_discovery_is_detected() -> None:
    source = FakeSource({"a": 1, "b": 1}, cheap=True)
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        source.dependencies["b"] = {"a"}
        result = await engine.refresh()
        state = (await engine.store.load_all())["b"]
    assert result.dependency_changed_count == 1
    assert operation.calls == [(REFRESH, "b")]
    assert state.dependencies == {"a"}


async def test_full_refresh_reads_and_refreshes_every_resource() -> None:
    source = FakeSource({"a": 1, "b": 2}, cheap=True)
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        source.snapshot_calls.clear()
        result = await engine.refresh(mode=RefreshMode.FULL)
    assert result.refreshed_count == 2
    assert result.unchanged_count == 2
    assert result.skipped_count == 0
    assert sorted(source.snapshot_calls) == ["a", "b"]
    assert sorted(operation.ids()) == ["a", "b"]


async def test_targeted_refresh_by_ids_ignores_change_and_other_resources() -> None:
    source = FakeSource({"a": 1, "b": 2, "c": 3})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        source.snapshot_calls.clear()
        result = await engine.refresh(resource_ids="b")
    assert result.request.mode is RefreshMode.TARGETED
    assert operation.calls == [(REFRESH, "b")]
    assert source.snapshot_calls == ["b"]
    assert (result.discovered_count, result.unchanged_count, result.skipped_count) == (
        3,
        1,
        2,
    )


async def test_targeted_refresh_combines_ids_and_selector() -> None:
    source = FakeSource(
        {"a": 1, "b": 2, "c": 3, "d": 4}, tags={"c": ["hot"], "d": ["hot", "x"]}
    )
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        result = await engine.refresh(resource_ids=["a"], selector=TagSelector(["hot"]))
    assert sorted(operation.ids()) == ["a", "c", "d"]
    assert result.skipped_count == 1


async def test_targeted_refresh_reports_unknown_ids_and_deletes_missing_ones() -> None:
    source = FakeSource({"a": 1, "gone": 2})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        del source.values["gone"]
        result = await engine.refresh(resource_ids=["gone", "ghost"])
        states = await engine.store.load_all()
    assert operation.calls == [(DELETE, "gone")]
    assert result.deleted_count == 1
    assert result.warnings == ("targeted resources were not discovered: 'ghost'",)
    assert "gone" not in states


async def test_targeted_selector_matching_nothing_warns() -> None:
    operation = RecordingOperation()
    async with RefreshEngine(FakeSource({"a": 1}), operation) as engine:
        result = await engine.refresh(selector=TagSelector("missing"))
    assert result.status is RefreshStatus.SUCCESS
    assert result.warnings == ("the selector matched no discovered resources",)
    assert operation.calls == []


async def test_targeted_refresh_lists_at_most_five_unknown_ids() -> None:
    async with RefreshEngine(FakeSource({}), RecordingOperation()) as engine:
        result = await engine.refresh(resource_ids=[f"x{index}" for index in range(7)])
    assert result.warnings[0] == (
        "targeted resources were not discovered: "
        "'x0', 'x1', 'x2', 'x3', 'x4' and 2 more"
    )


async def test_dependency_aware_refresh_follows_dependency_order() -> None:
    source = FakeSource(
        {"A": 1, "B": 1, "C": 1}, dependencies={"A": {"B"}, "B": {"C"}}, cheap=True
    )
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        source.snapshot_calls.clear()
        source.values["C"] = 2
        result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
    assert operation.ids() == ["C", "B", "A"]
    assert (result.modified_count, result.impacted_count) == (1, 2)
    assert result.skipped_count == 0
    assert sorted(source.snapshot_calls) == ["A", "B", "C"]


async def test_incremental_refresh_does_not_refresh_dependents() -> None:
    source = FakeSource({"A": 1, "B": 1}, dependencies={"A": {"B"}})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        source.values["B"] = 2
        result = await engine.refresh()
    assert operation.ids() == ["B"]
    assert result.impacted_count == 0


async def test_dependency_aware_refresh_ignores_undiscovered_dependents() -> None:
    source = FakeSource({"base": 1, "derived": 1}, dependencies={"derived": {"base"}})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        source.values = {"base": 2}
        source.complete = False
        result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
    assert operation.ids() == ["base"]
    assert result.impacted_count == 0
    assert result.deleted_count == 0


async def test_deleted_dependency_runs_before_its_dependents() -> None:
    source = FakeSource(
        {"base": 1, "mid": 1, "top": 1}, dependencies={"mid": {"base"}, "top": {"mid"}}
    )
    operation = RecordingOperation()
    async with RefreshEngine(
        source, operation, config=RefreshConfig(max_concurrency=1)
    ) as engine:
        await engine.refresh()
        operation.calls.clear()
        del source.values["base"]
        result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
    assert operation.calls == [(DELETE, "base"), (REFRESH, "mid"), (REFRESH, "top")]
    assert (result.deleted_count, result.impacted_count) == (1, 2)


async def test_dependents_are_deleted_before_their_dependencies() -> None:
    source = FakeSource({"base": 1, "top": 1}, dependencies={"top": {"base"}})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        source.values = {}
        result = await engine.refresh()
    assert operation.calls == [(DELETE, "top"), (DELETE, "base")]
    assert result.deleted_count == 2
    assert await engine.store.load_all() == {}


async def test_deletion_receives_a_lightweight_resource_without_snapshot() -> None:
    source = FakeSource({"a": 1})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        source.values = {}
        await engine.refresh()
    assert operation.calls[-1] == (DELETE, "a")
    assert operation.snapshots["a"] is None


async def test_incomplete_discovery_never_infers_deletions() -> None:
    source = FakeSource({"a": 1, "b": 2})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        source.values = {"a": 1}
        source.complete = False
        result = await engine.refresh()
        states = await engine.store.load_all()
    assert result.deleted_count == 0
    assert set(states) == {"a", "b"}
    assert result.warnings == (
        "deletion of 1 undiscovered resources was suppressed because discovery "
        "was incomplete",
    )


async def test_dependency_cycle_fails_only_affected_resources() -> None:
    source = FakeSource(
        {"a": 1, "b": 1, "c": 1, "d": 1},
        dependencies={"a": {"b"}, "b": {"a"}, "d": {"a"}},
    )
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        result = await engine.refresh()
        states = await engine.store.load_all()
    assert result.status is RefreshStatus.PARTIAL
    assert operation.ids() == ["c"]
    issues = {issue.resource_id: issue for issue in result.errors}
    assert issues["a"].message == "dependency cycle detected: a -> b -> a"
    assert issues["b"].message == "dependency cycle detected: b -> a -> b"
    assert issues["b"].exception_type == "DependencyCycleError"
    assert issues["d"].message == "not run because dependency 'a' failed"
    assert set(states) == {"c"}


async def test_self_dependency_is_reported_as_a_cycle() -> None:
    source = FakeSource({"a": 1}, dependencies={"a": {"a"}})
    async with RefreshEngine(source, RecordingOperation()) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    assert result.errors[0].message == "dependency cycle detected: a -> a"


async def test_unchanged_resource_keeps_its_state_current_without_a_refresh() -> None:
    source = FakeSource({"a": {"title": "x"}})
    store = InMemoryStateStore()
    operation = RecordingOperation()
    async with RefreshEngine(
        source, operation, store=store, fingerprint=ContentHash()
    ) as engine:
        await engine.refresh()
        before = (await store.load_all())["a"]
        operation.calls.clear()
        source.cheap = True
        result = await engine.refresh()
        after = (await store.load_all())["a"]
    assert result.unchanged_count == 1
    assert operation.calls == []
    assert after.cheap_indicator == repr({"title": "x"})
    assert after.fingerprint == before.fingerprint
    assert after.refreshed_at == before.refreshed_at


async def test_failed_refresh_of_an_impacted_resource_is_retried_later() -> None:
    source = FakeSource({"base": 1, "derived": 1}, dependencies={"derived": {"base"}})
    operation = RecordingOperation(failures={"derived": RuntimeError("down")})
    async with RefreshEngine(source, operation) as engine:
        operation.failures_remaining["derived"] = 0
        await engine.refresh()
        operation.calls.clear()
        source.values["base"] = 2
        operation.failures_remaining["derived"] = 1
        failed = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
        marker = (await engine.store.load_all())["derived"]
        operation.calls.clear()
        retried = await engine.refresh()
    assert failed.status is RefreshStatus.PARTIAL
    assert marker.fingerprint.algorithm == "refresh-engine:retry"
    assert marker.fingerprint.value == failed.refresh_id
    assert marker.cheap_indicator is None
    assert operation.calls == [(REFRESH, "derived")]
    assert retried.modified_count == 1
    assert retried.status is RefreshStatus.SUCCESS


async def test_failed_full_refresh_of_an_unchanged_resource_is_retried() -> None:
    source = FakeSource({"a": 1, "b": 1})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.failures["a"] = RuntimeError("down")
        await engine.refresh(mode=RefreshMode.FULL)
        operation.failures.clear()
        operation.calls.clear()
        result = await engine.refresh()
    assert operation.calls == [(REFRESH, "a")]
    assert result.modified_count == 1


async def test_snapshot_version_overrides_discovery_version() -> None:
    class VersionedSource(FakeSource):
        async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
            snapshot = await super().snapshot(resource)
            return ResourceSnapshot(
                snapshot.resource_id, content=snapshot.content, version="snap"
            )

    async with RefreshEngine(VersionedSource({"a": 1}), RecordingOperation()) as engine:
        await engine.refresh()
        state = (await engine.store.load_all())["a"]
    assert state.version == "snap"


async def test_synchronous_discovery_iterables_are_supported() -> None:
    operation = RecordingOperation()
    async with RefreshEngine(
        FakeSource({"a": 1, "b": 2}, asynchronous=False), operation
    ) as engine:
        result = await engine.refresh()
    assert result.refreshed_count == 2


async def test_cheap_matching_yields_to_the_event_loop_periodically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FakeSource(
        {str(index): index for index in range(3_000)}, cheap=True, asynchronous=False
    )
    async with RefreshEngine(source, RecordingOperation()) as engine:
        await engine.refresh()
        original = asyncio.sleep
        yields = 0

        async def counting_sleep(delay: float, result: object = None) -> object:
            nonlocal yields
            if delay == 0:
                yields += 1
            return await original(delay, result)

        monkeypatch.setattr(asyncio, "sleep", counting_sleep)
        result = await engine.refresh()
    assert result.unchanged_count == 3_000
    assert yields == 2
