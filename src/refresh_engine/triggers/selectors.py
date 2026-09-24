"""Generic resource selectors."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from refresh_engine.api.abc import ResourceSelectorABC
from refresh_engine.core.models import Resource


@dataclass(frozen=True, slots=True)
class IDSelector(ResourceSelectorABC):
    resource_ids: frozenset[str]

    def __init__(self, resource_ids: Iterable[str]) -> None:
        object.__setattr__(self, "resource_ids", frozenset(resource_ids))

    def matches(self, resource: Resource) -> bool:
        return resource.resource_id in self.resource_ids


@dataclass(frozen=True, slots=True)
class TagSelector(ResourceSelectorABC):
    tags: frozenset[str]
    match_all: bool = True

    def __init__(self, tags: Iterable[str], match_all: bool = True) -> None:
        object.__setattr__(self, "tags", frozenset(tags))
        object.__setattr__(self, "match_all", match_all)

    def matches(self, resource: Resource) -> bool:
        resource_tags = frozenset(resource.metadata.get("tags", ()))
        return (
            self.tags <= resource_tags
            if self.match_all
            else bool(self.tags & resource_tags)
        )


@dataclass(frozen=True, slots=True)
class PredicateSelector(ResourceSelectorABC):
    predicate: Callable[[Resource], bool]

    def matches(self, resource: Resource) -> bool:
        return self.predicate(resource)
