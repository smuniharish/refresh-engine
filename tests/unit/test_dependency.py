from __future__ import annotations

import pytest

from refresh_engine import (
    Change,
    ChangeSet,
    ChangeType,
    DependencyGraph,
    ImpactAnalyzer,
)
from refresh_engine.errors import DependencyCycleError


def test_reverse_impact_and_topological_order() -> None:
    graph = DependencyGraph()
    graph.add_dependency("A", "B")
    graph.add_dependency("B", "C")
    changes = ChangeSet((Change("C", ChangeType.MODIFIED),))
    impact = ImpactAnalyzer().analyze(changes, graph)
    assert impact.directly_changed == {"C"}
    assert impact.indirectly_impacted == {"A", "B"}
    assert graph.topological_levels(impact.all_impacted) == (("C",), ("B",), ("A",))


def test_dependency_lookup_and_removal() -> None:
    graph = DependencyGraph()
    graph.add_dependency("dependent", "dependency")
    assert graph.dependencies_of("dependent") == {"dependency"}
    assert graph.dependents_of("dependency") == {"dependent"}
    graph.remove_dependency("dependent", "dependency")
    assert graph.dependencies_of("dependent") == set()
    assert graph.dependents_of("dependency") == set()
    assert graph.dependencies_of("missing") == set()


def test_unrelated_subgraph_is_unaffected() -> None:
    graph = DependencyGraph()
    graph.add_dependency("A", "B")
    graph.add_dependency("C", "D")
    impact = ImpactAnalyzer().analyze(
        ChangeSet((Change("B", ChangeType.MODIFIED),)), graph
    )
    assert impact.all_impacted == {"A", "B"}
    assert impact.unaffected == {"C", "D"}


def test_cycle_reports_path() -> None:
    graph = DependencyGraph()
    graph.add_dependency("A", "B")
    graph.add_dependency("B", "C")
    graph.add_dependency("C", "A")
    with pytest.raises(DependencyCycleError) as caught:
        graph.topological_levels()
    assert caught.value.cycle[0] == caught.value.cycle[-1]
