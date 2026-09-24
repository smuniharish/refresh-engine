"""Change classification."""

from __future__ import annotations

from collections.abc import Mapping

from refresh_engine.core.models import (
    Change,
    ChangeSet,
    ChangeType,
    ResourceState,
)


class ChangeDetector:
    """Compare current candidates with the last successful state."""

    def detect(
        self,
        previous: Mapping[str, ResourceState],
        current: Mapping[str, ResourceState],
        *,
        discovery_complete: bool,
        diagnostics: tuple[str, ...] = (),
    ) -> ChangeSet:
        changes: list[Change] = []
        for resource_id, state in current.items():
            old = previous.get(resource_id)
            if old is None:
                kind = ChangeType.ADDED
            elif old.fingerprint != state.fingerprint:
                kind = ChangeType.MODIFIED
            elif old.dependencies != state.dependencies:
                kind = ChangeType.DEPENDENCY_CHANGED
            else:
                kind = ChangeType.UNCHANGED
            changes.append(Change(resource_id, kind, old, state))

        if discovery_complete:
            for resource_id in previous.keys() - current.keys():
                changes.append(
                    Change(
                        resource_id,
                        ChangeType.DELETED,
                        previous[resource_id],
                        None,
                    )
                )
        elif previous.keys() - current.keys():
            diagnostics += (
                "deletion inference suppressed because discovery was incomplete",
            )
        changes.sort(key=lambda change: change.resource_id)
        return ChangeSet(tuple(changes), diagnostics)
