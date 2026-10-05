from __future__ import annotations

from collections.abc import Iterable

from refresh_engine import (
    PlanAction,
    RefreshMode,
    Resource,
    ResourceFingerprint,
    ResourceState,
)
from refresh_engine._internal.context import ChangeType, Observation
from refresh_engine._internal.planning import Plan, build_plan

FINGERPRINT = ResourceFingerprint("test", "value")


def state(resource_id: str, dependencies: Iterable[str] = ()) -> ResourceState:
    return ResourceState(resource_id, FINGERPRINT, dependencies=frozenset(dependencies))


def observed(
    resource_id: str,
    change: ChangeType = ChangeType.MODIFIED,
    dependencies: Iterable[str] = (),
    previous_dependencies: Iterable[str] | None = None,
) -> Observation:
    previous = (
        None
        if change is ChangeType.ADDED
        else state(
            resource_id,
            dependencies if previous_dependencies is None else previous_dependencies,
        )
    )
    return Observation(
        resource=Resource(resource_id),
        fingerprint=FINGERPRINT,
        version=None,
        metadata={},
        dependencies=frozenset(dependencies),
        previous=previous,
        change=change,
    )


def plan_for(
    mode: RefreshMode,
    observations: Iterable[Observation],
    deleted: Iterable[str] = (),
    previous: Iterable[ResourceState] = (),
) -> Plan:
    by_id = {item.resource.resource_id: item for item in observations}
    stored = {item.resource_id: item for item in previous}
    for item in by_id.values():
        if item.previous is not None:
            stored.setdefault(item.resource.resource_id, item.previous)
    return build_plan(mode, by_id, sorted(deleted), stored)


def test_plan_without_dependencies_has_no_graph() -> None:
    plan = plan_for(
        RefreshMode.INCREMENTAL,
        [
            observed("b"),
            observed("a", ChangeType.ADDED),
            observed("c", ChangeType.UNCHANGED),
        ],
        deleted=["z"],
        previous=[state("z")],
    )
    assert plan.resource_ids == ["a", "b", "z"]
    assert plan.actions == [PlanAction.REFRESH, PlanAction.REFRESH, PlanAction.DELETE]
    assert plan.refresh_ids == ["a", "b"]
    assert (plan.dependents, plan.indegree, plan.cycles) == (None, None, {})


def test_full_and_targeted_plans_select_every_observation() -> None:
    observations = [observed("a", ChangeType.UNCHANGED), observed("b")]
    for mode in (RefreshMode.FULL, RefreshMode.TARGETED):
        assert plan_for(mode, observations).refresh_ids == ["a", "b"]


def test_dependency_aware_plan_follows_dependents_through_unobserved_resources() -> (
    None
):
    plan = plan_for(
        RefreshMode.DEPENDENCY_AWARE,
        [
            observed("base"),
            observed("top", ChangeType.UNCHANGED, dependencies={"middle"}),
            observed("other", ChangeType.UNCHANGED),
        ],
        previous=[state("middle", {"base"})],
    )
    assert plan.refresh_ids == ["base", "top"]
    assert plan.impacted == 1


def test_dependency_aware_plan_propagates_from_deletions() -> None:
    plan = plan_for(
        RefreshMode.DEPENDENCY_AWARE,
        [observed("child", ChangeType.UNCHANGED, dependencies={"gone"})],
        deleted=["gone"],
        previous=[state("gone")],
    )
    assert plan.resource_ids == ["child", "gone"]
    assert plan.impacted == 1
    assert plan.dependents == [[], [0]]
    assert plan.indegree == [1, 0]


def test_dependencies_order_refreshes_and_deletions() -> None:
    plan = plan_for(
        RefreshMode.INCREMENTAL,
        [
            observed("base"),
            observed("top", dependencies={"base"}, previous_dependencies={"old"}),
        ],
        deleted=["old", "older"],
        previous=[state("old", {"older"}), state("older")],
    )
    assert plan.resource_ids == ["base", "top", "old", "older"]
    assert plan.indegree == [0, 2, 0, 1]
    assert plan.dependents == [[1], [], [1, 3], []]


def test_cycles_are_isolated_and_reported_with_a_readable_path() -> None:
    plan = plan_for(
        RefreshMode.INCREMENTAL,
        [
            observed("a", dependencies={"b"}),
            observed("b", dependencies={"c"}),
            observed("c", dependencies={"a"}),
            observed("d"),
            observed("e", dependencies={"a"}),
        ],
    )
    assert set(plan.cycles) == {0, 1, 2}
    assert plan.cycles[0].cycle == ("a", "b", "c", "a")
    assert plan.cycles[1].cycle == ("b", "c", "a", "b")
    assert plan.cycles[2].cycle == ("c", "a", "b", "c")


def test_separate_cycles_and_self_dependencies_are_reported_separately() -> None:
    plan = plan_for(
        RefreshMode.INCREMENTAL,
        [
            observed("a", dependencies={"b"}),
            observed("b", dependencies={"a"}),
            observed("x", dependencies={"x"}),
            observed("y", dependencies={"x", "a"}),
        ],
    )
    assert set(plan.cycles) == {0, 1, 2}
    assert plan.cycles[0].cycle == ("a", "b", "a")
    assert plan.cycles[1].cycle == ("b", "a", "b")
    assert plan.cycles[2].cycle == ("x", "x")


def test_cycle_paths_are_shortest_paths_through_the_component() -> None:
    plan = plan_for(
        RefreshMode.INCREMENTAL,
        [
            observed("a", dependencies={"b", "c"}),
            observed("b", dependencies={"d"}),
            observed("c", dependencies={"d"}),
            observed("d", dependencies={"a"}),
        ],
    )
    assert plan.cycles[0].cycle == ("a", "b", "d", "a")


def test_dependency_aware_impact_visits_shared_dependents_once() -> None:
    plan = plan_for(
        RefreshMode.DEPENDENCY_AWARE,
        [
            observed("base"),
            observed("left", ChangeType.UNCHANGED, dependencies={"base"}),
            observed("right", ChangeType.UNCHANGED, dependencies={"base"}),
            observed("top", ChangeType.UNCHANGED, dependencies={"left", "right"}),
        ],
    )
    assert plan.refresh_ids == ["base", "left", "right", "top"]
    assert plan.impacted == 3
    assert plan.indegree == [0, 1, 1, 2]


def test_deleted_resources_depending_on_surviving_ones_are_unordered() -> None:
    plan = plan_for(
        RefreshMode.INCREMENTAL,
        [observed("kept")],
        deleted=["gone"],
        previous=[state("gone", {"kept"})],
    )
    assert plan.resource_ids == ["kept", "gone"]
    assert plan.dependents is None
