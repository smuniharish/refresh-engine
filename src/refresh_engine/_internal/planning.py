"""Selection and dependency ordering of the operations of one refresh."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from refresh_engine._internal.context import ChangeType, Observation
from refresh_engine.errors import DependencyCycleError
from refresh_engine.models import PlanAction, RefreshMode, ResourceState


@dataclass(slots=True, eq=False)
class Plan:
    """Operations of one refresh and the dependencies between them.

    Items are indexed by position. `dependents[i]` lists the items that must wait
    for item `i`; `indegree[i]` counts the items item `i` waits for. Both are
    `None` when no item depends on another.
    """

    resource_ids: list[str]
    actions: list[PlanAction]
    impacted: int = 0
    dependents: list[list[int]] | None = None
    indegree: list[int] | None = None
    cycles: dict[int, DependencyCycleError] = field(default_factory=dict)

    @property
    def refresh_ids(self) -> list[str]:
        """IDs of the resources planned for a refresh operation."""
        return [
            resource_id
            for resource_id, action in zip(self.resource_ids, self.actions, strict=True)
            if action is PlanAction.REFRESH
        ]


def build_plan(
    mode: RefreshMode,
    observations: Mapping[str, Observation],
    deleted: list[str],
    previous: Mapping[str, ResourceState],
) -> Plan:
    """Select and order the operations for `mode`."""
    impacted: set[str] = set()
    if mode is RefreshMode.FULL or mode is RefreshMode.TARGETED:
        selected: Iterable[str] = observations
    else:
        changed = {
            resource_id
            for resource_id, observation in observations.items()
            if observation.change is not ChangeType.UNCHANGED
        }
        if mode is RefreshMode.DEPENDENCY_AWARE:
            impacted = _impacted(changed | set(deleted), observations, previous)
            impacted -= changed
        selected = changed | impacted
    refreshes = sorted(selected)
    resource_ids = [*refreshes, *deleted]
    actions = [PlanAction.REFRESH] * len(refreshes) + [PlanAction.DELETE] * len(deleted)
    plan = Plan(resource_ids, actions, impacted=len(impacted))
    predecessors = _predecessors(refreshes, deleted, observations, previous)
    if any(predecessors):
        _order(plan, predecessors)
    return plan


def _impacted(
    direct: set[str],
    observations: Mapping[str, Observation],
    previous: Mapping[str, ResourceState],
) -> set[str]:
    """Return observed resources that transitively depend on a direct change."""
    dependencies: dict[str, frozenset[str]] = {
        resource_id: frozenset(state.dependencies)
        for resource_id, state in previous.items()
    }
    for resource_id, observation in observations.items():
        dependencies[resource_id] = observation.dependencies
    dependents: defaultdict[str, list[str]] = defaultdict(list)
    for resource_id, required in dependencies.items():
        for dependency in required:
            dependents[dependency].append(resource_id)
    reached = set(direct)
    queue = deque(direct)
    while queue:
        for dependent in dependents.get(queue.popleft(), ()):
            if dependent not in reached:
                reached.add(dependent)
                queue.append(dependent)
    return {resource_id for resource_id in reached if resource_id in observations}


def _predecessors(
    refreshes: list[str],
    deleted: list[str],
    observations: Mapping[str, Observation],
    previous: Mapping[str, ResourceState],
) -> list[set[int]]:
    """Return, for each item, the items that must complete before it."""
    refresh_index = {resource_id: index for index, resource_id in enumerate(refreshes)}
    delete_index = {
        resource_id: len(refreshes) + offset
        for offset, resource_id in enumerate(deleted)
    }
    predecessors: list[set[int]] = [set() for _ in range(len(refreshes) + len(deleted))]
    for index, resource_id in enumerate(refreshes):
        observation = observations[resource_id]
        for dependency in observation.dependencies:
            before = refresh_index.get(dependency)
            if before is not None:
                predecessors[index].add(before)
        if delete_index:
            related = set(observation.dependencies)
            if observation.previous is not None:
                related.update(observation.previous.dependencies)
            for dependency in related:
                before = delete_index.get(dependency)
                if before is not None:
                    predecessors[index].add(before)
    for resource_id, index in delete_index.items():
        for dependency in previous[resource_id].dependencies:
            after = delete_index.get(dependency)
            if after is not None:
                predecessors[after].add(index)
    return predecessors


def _order(plan: Plan, predecessors: list[set[int]]) -> None:
    """Fill in the dependency graph of `plan` and isolate dependency cycles."""
    count = len(predecessors)
    dependents: list[list[int]] = [[] for _ in range(count)]
    indegree = [len(before) for before in predecessors]
    for index, before in enumerate(predecessors):
        for item in before:
            dependents[item].append(index)
    plan.dependents = dependents
    plan.indegree = indegree
    remaining = list(indegree)
    ready = deque(index for index in range(count) if remaining[index] == 0)
    processed = 0
    while ready:
        index = ready.popleft()
        processed += 1
        for dependent in dependents[index]:
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                ready.append(dependent)
    if processed == count:
        return
    blocked = {index for index in range(count) if remaining[index] > 0}
    for component in _strongly_connected(blocked, predecessors):
        if len(component) == 1:
            (index,) = component
            if index not in predecessors[index]:
                continue
        for index in component:
            plan.cycles[index] = DependencyCycleError(
                _cycle(index, component, predecessors, plan.resource_ids)
            )


def _strongly_connected(
    nodes: set[int], predecessors: list[set[int]]
) -> list[set[int]]:
    """Return the strongly connected components of the subgraph on `nodes`."""
    index_of: dict[int, int] = {}
    low: dict[int, int] = {}
    stack: list[int] = []
    on_stack: set[int] = set()
    components: list[set[int]] = []
    counter = 0
    for root in sorted(nodes):
        if root in index_of:
            continue
        work = [(root, iter(sorted(predecessors[root] & nodes)))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            for child in children:
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(predecessors[child] & nodes))))
                    break
                if child in on_stack:
                    low[node] = min(low[node], index_of[child])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index_of[node]:
                    component: set[int] = set()
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.add(member)
                        if member == node:
                            break
                    components.append(component)
    return components


def _cycle(
    start: int,
    component: set[int],
    predecessors: list[set[int]],
    resource_ids: list[str],
) -> list[str]:
    """Return the shortest dependency cycle through `start`, as resource IDs.

    Each ID depends on the next one, and the first ID is repeated at the end.
    """
    parents: dict[int, int] = {}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for dependency in sorted(predecessors[node] & component):
            if dependency == start:
                path = [node]
                while path[-1] != start:
                    path.append(parents[path[-1]])
                cycle = [resource_ids[index] for index in reversed(path)]
                return [*cycle, resource_ids[start]]
            if dependency not in parents:
                parents[dependency] = node
                queue.append(dependency)
    raise AssertionError("component contains no cycle")  # pragma: no cover
