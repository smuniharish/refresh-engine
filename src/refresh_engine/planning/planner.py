"""Refresh plan construction."""

from __future__ import annotations

from collections.abc import Mapping

from refresh_engine.core.models import (
    ChangeSet,
    ChangeType,
    ImpactSet,
    PlanAction,
    PlanItem,
    RefreshMode,
    RefreshPlan,
    RefreshRequest,
    ResourceState,
)
from refresh_engine.planning.dependency import DependencyGraph


class RefreshPlanner:
    def plan(
        self,
        refresh_id: str,
        request: RefreshRequest,
        changes: ChangeSet,
        impact: ImpactSet,
        graph: DependencyGraph,
        current: Mapping[str, ResourceState],
    ) -> RefreshPlan:
        changed = {change.resource_id: change for change in changes.changes}
        if request.mode is RefreshMode.FULL:
            selected = set(current) | {
                resource_id
                for resource_id, change in changed.items()
                if change.change_type is ChangeType.DELETED
            }
        elif request.mode is RefreshMode.TARGETED:
            selected = set(request.resource_ids) & set(changed)
        elif request.mode is RefreshMode.DEPENDENCY_AWARE:
            selected = set(impact.all_impacted)
        else:
            selected = {
                resource_id
                for resource_id, change in changed.items()
                if change.change_type is not ChangeType.UNCHANGED
            }

        deleted = {
            resource_id
            for resource_id in selected
            if changed.get(resource_id)
            and changed[resource_id].change_type is ChangeType.DELETED
        }
        refreshable = selected - deleted
        levels = []
        for level in graph.topological_levels(refreshable):
            levels.append(
                tuple(
                    PlanItem(
                        resource_id,
                        PlanAction.REFRESH,
                        current.get(resource_id),
                        graph.dependencies_of(resource_id),
                    )
                    for resource_id in level
                )
            )
        if deleted:
            levels.append(
                tuple(
                    PlanItem(
                        resource_id,
                        PlanAction.DELETE,
                        None,
                        frozenset(),
                    )
                    for resource_id in sorted(deleted)
                )
            )
        return RefreshPlan(
            refresh_id,
            request,
            tuple(levels),
            skipped_count=max(0, len(changes.changes) - len(selected)),
        )
