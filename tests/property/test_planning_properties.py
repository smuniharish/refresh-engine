from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given
from hypothesis import strategies as st

from refresh_engine import (
    RefreshMode,
    RefreshRequest,
    Resource,
    ResourceFingerprint,
    ResourceState,
    TagSelector,
)
from refresh_engine._internal.context import ChangeType, Observation
from refresh_engine._internal.coordinator import coalesce
from refresh_engine._internal.planning import Plan, build_plan

NAMES = tuple(f"r{index}" for index in range(7))
FINGERPRINT = ResourceFingerprint("test", "value")

type Node = tuple[str, str]


@dataclass(frozen=True)
class Scenario:
    mode: RefreshMode
    changes: dict[str, ChangeType]
    dependencies: dict[str, frozenset[str]]
    previous_dependencies: dict[str, frozenset[str]]
    deleted: frozenset[str]
    stored_only: frozenset[str]

    def observations(self) -> dict[str, Observation]:
        return {
            name: Observation(
                resource=Resource(name),
                fingerprint=FINGERPRINT,
                version=None,
                metadata={},
                dependencies=self.dependencies[name],
                previous=(
                    None
                    if change is ChangeType.ADDED
                    else ResourceState(
                        name,
                        FINGERPRINT,
                        dependencies=self.previous_dependencies[name],
                    )
                ),
                change=change,
            )
            for name, change in self.changes.items()
        }

    def previous(self) -> dict[str, ResourceState]:
        stored = {
            name: ResourceState(
                name, FINGERPRINT, dependencies=self.previous_dependencies[name]
            )
            for name in self.deleted | self.stored_only
        }
        for name, change in self.changes.items():
            if change is not ChangeType.ADDED:
                stored[name] = ResourceState(
                    name, FINGERPRINT, dependencies=self.previous_dependencies[name]
                )
        return stored


@st.composite
def scenarios(draw: st.DrawFn) -> Scenario:
    names = draw(st.lists(st.sampled_from(NAMES), min_size=1, unique=True))
    observed = draw(st.lists(st.sampled_from(names), unique=True))
    rest = [name for name in names if name not in observed]
    deleted = frozenset(
        draw(st.lists(st.sampled_from(rest), unique=True)) if rest else []
    )
    stored_only = frozenset(name for name in rest if name not in deleted)
    subsets = st.frozensets(st.sampled_from(names), max_size=3)
    return Scenario(
        mode=draw(st.sampled_from(RefreshMode)),
        changes={name: draw(st.sampled_from(ChangeType)) for name in observed},
        dependencies={name: draw(subsets) for name in names},
        previous_dependencies={name: draw(subsets) for name in names},
        deleted=deleted,
        stored_only=stored_only,
    )


def expected_refreshes(scenario: Scenario) -> tuple[set[str], set[str]]:
    observed = set(scenario.changes)
    changed = {
        name
        for name, change in scenario.changes.items()
        if change is not ChangeType.UNCHANGED
    }
    if scenario.mode in (RefreshMode.FULL, RefreshMode.TARGETED):
        return observed, set()
    if scenario.mode is RefreshMode.INCREMENTAL:
        return changed, set()
    effective = {
        name: scenario.previous_dependencies[name] for name in scenario.previous()
    }
    effective.update({name: scenario.dependencies[name] for name in observed})
    reached = changed | set(scenario.deleted)
    grew = True
    while grew:
        grew = False
        for name, needs in effective.items():
            if name not in reached and needs & reached:
                reached.add(name)
                grew = True
    impacted = (reached & observed) - changed
    return changed | impacted, impacted


def expected_edges(scenario: Scenario, refreshes: set[str]) -> set[tuple[Node, Node]]:
    edges: set[tuple[Node, Node]] = set()
    stored = scenario.previous()
    for name in refreshes:
        for dependency in scenario.dependencies[name] & refreshes:
            edges.add((("R", dependency), ("R", name)))
        related = set(scenario.dependencies[name])
        if name in stored:
            related |= scenario.previous_dependencies[name]
        for dependency in related & scenario.deleted:
            edges.add((("D", dependency), ("R", name)))
    for name in scenario.deleted:
        for dependency in scenario.previous_dependencies[name] & scenario.deleted:
            edges.add((("D", name), ("D", dependency)))
    return edges


def plan_edges(plan: Plan) -> set[tuple[Node, Node]]:
    nodes = [
        (action.value[0].upper(), name)
        for action, name in zip(plan.actions, plan.resource_ids, strict=True)
    ]
    if plan.dependents is None:
        return set()
    return {
        (nodes[source], nodes[target])
        for source, targets in enumerate(plan.dependents)
        for target in targets
    }


def reaches(edges: set[tuple[Node, Node]], start: Node, goal: Node) -> bool:
    frontier = [target for source, target in edges if source == start]
    seen: set[Node] = set()
    while frontier:
        node = frontier.pop()
        if node == goal:
            return True
        if node not in seen:
            seen.add(node)
            frontier.extend(target for source, target in edges if source == node)
    return False


@given(scenarios())
def test_plans_match_the_reference_model(scenario: Scenario) -> None:
    plan = build_plan(
        scenario.mode,
        scenario.observations(),
        sorted(scenario.deleted),
        scenario.previous(),
    )
    refreshes, impacted = expected_refreshes(scenario)
    assert plan.refresh_ids == sorted(refreshes)
    assert plan.resource_ids[len(refreshes) :] == sorted(scenario.deleted)
    assert plan.impacted == len(impacted)

    edges = expected_edges(scenario, refreshes)
    assert plan_edges(plan) == edges
    if plan.indegree is not None:
        assert plan.indegree == [
            sum(1 for _, target in edges if target == node)
            for node in (
                (action.value[0].upper(), name)
                for action, name in zip(plan.actions, plan.resource_ids, strict=True)
            )
        ]

    nodes = [
        (action.value[0].upper(), name)
        for action, name in zip(plan.actions, plan.resource_ids, strict=True)
    ]
    cyclic = {index for index, node in enumerate(nodes) if reaches(edges, node, node)}
    assert set(plan.cycles) == cyclic
    for index, error in plan.cycles.items():
        cycle = error.cycle
        assert cycle[0] == cycle[-1] == nodes[index][1]
        members = {nodes[member][1] for member in cyclic}
        assert set(cycle) <= members


TAGS = ("hot", "cold")
requests = st.one_of(
    st.sampled_from(
        [RefreshMode.INCREMENTAL, RefreshMode.FULL, RefreshMode.DEPENDENCY_AWARE]
    ).map(lambda mode: RefreshRequest(mode=mode)),
    st.tuples(
        st.frozensets(st.sampled_from(NAMES), max_size=3),
        st.none() | st.sampled_from(TAGS).map(TagSelector),
    )
    .filter(lambda pair: bool(pair[0]) or pair[1] is not None)
    .map(
        lambda pair: RefreshRequest(
            mode=RefreshMode.TARGETED, resource_ids=pair[0], selector=pair[1]
        )
    ),
)
resources = st.builds(
    lambda name, tags: Resource(name, metadata={"tags": sorted(tags)}),
    st.sampled_from(NAMES),
    st.frozensets(st.sampled_from(TAGS)),
)
COVERS = {
    RefreshMode.FULL: set(RefreshMode),
    RefreshMode.DEPENDENCY_AWARE: {
        RefreshMode.DEPENDENCY_AWARE,
        RefreshMode.INCREMENTAL,
    },
    RefreshMode.INCREMENTAL: {RefreshMode.INCREMENTAL},
    RefreshMode.TARGETED: {RefreshMode.TARGETED},
}


def selects(request: RefreshRequest, resource: Resource) -> bool:
    return resource.resource_id in request.resource_ids or (
        request.selector is not None and request.selector.matches(resource)
    )


@given(requests, requests, resources)
def test_coalesced_requests_cover_exactly_both_intents(
    first: RefreshRequest, second: RefreshRequest, resource: Resource
) -> None:
    merged = coalesce(first, second)
    reverse = coalesce(second, first)
    compatible = second.mode in COVERS[first.mode] or first.mode in COVERS[second.mode]
    assert (merged is not None) is compatible
    assert (reverse is not None) is compatible
    if merged is None or reverse is None:
        return
    assert merged.mode is reverse.mode
    assert merged.mode in (first.mode, second.mode)
    assert {first.mode, second.mode} <= COVERS[merged.mode]
    if merged.mode is RefreshMode.TARGETED:
        assert merged.resource_ids == first.resource_ids | second.resource_ids
        expected = selects(first, resource) or selects(second, resource)
        assert selects(merged, resource) is expected
        assert selects(reverse, resource) is expected
    else:
        assert (merged.resource_ids, merged.selector) == (frozenset(), None)
