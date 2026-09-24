"""Dependency graph and impact analysis."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

import networkx as nx

from refresh_engine.core.models import ChangeSet, ChangeType, ImpactSet
from refresh_engine.errors import DependencyCycleError


class DependencyGraph:
    """Directed graph where a node maps to the resources it depends on."""

    def __init__(self) -> None:
        self._graph: nx.DiGraph[str] = nx.DiGraph()

    @property
    def nodes(self) -> frozenset[str]:
        return frozenset(self._graph.nodes)

    def add_resource(self, resource_id: str) -> None:
        self._graph.add_node(resource_id)

    def add_dependency(self, resource_id: str, dependency_id: str) -> None:
        self._graph.add_edge(dependency_id, resource_id)

    def remove_dependency(self, resource_id: str, dependency_id: str) -> None:
        if self._graph.has_edge(dependency_id, resource_id):
            self._graph.remove_edge(dependency_id, resource_id)

    def dependencies_of(self, resource_id: str) -> frozenset[str]:
        if resource_id not in self._graph:
            return frozenset()
        return frozenset(self._graph.predecessors(resource_id))

    def dependents_of(self, resource_id: str) -> frozenset[str]:
        if resource_id not in self._graph:
            return frozenset()
        return frozenset(self._graph.successors(resource_id))

    def transitive_dependents(self, resource_ids: Iterable[str]) -> frozenset[str]:
        selected = set(resource_ids)
        impacted = set(selected)
        for resource_id in selected & self.nodes:
            impacted.update(cast(set[str], nx.descendants(self._graph, resource_id)))
        return frozenset(impacted)

    def topological_levels(
        self, resource_ids: Iterable[str] | None = None
    ) -> tuple[tuple[str, ...], ...]:
        selected = self.nodes if resource_ids is None else frozenset(resource_ids)
        graph = self._graph.subgraph(selected)
        try:
            generations = cast(
                Iterable[Iterable[str]], nx.topological_generations(graph)
            )
            return tuple(tuple(sorted(generation)) for generation in generations)
        except nx.NetworkXUnfeasible as error:
            cycle_edges = cast(list[tuple[str, str]], nx.find_cycle(graph))
            cycle = (*tuple(edge[0] for edge in cycle_edges), cycle_edges[0][0])
            raise DependencyCycleError(cycle) from error


class ImpactAnalyzer:
    def analyze(self, changes: ChangeSet, graph: DependencyGraph) -> ImpactSet:
        direct = frozenset(
            change.resource_id
            for change in changes.changes
            if change.change_type is not ChangeType.UNCHANGED
        )
        impacted = graph.transitive_dependents(direct)
        return ImpactSet(
            directly_changed=direct,
            indirectly_impacted=impacted - direct,
            unaffected=graph.nodes - impacted,
        )
