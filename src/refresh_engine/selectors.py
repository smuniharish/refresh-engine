"""Reusable resource selectors.

Pass a selector to a `TARGETED` refresh to choose resources by their discovery
data. Selectors compose with `&`, `|`, and `~`; see
[`ResourceSelectorABC`][refresh_engine.ResourceSelectorABC].
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from refresh_engine._internal.values import id_set, require_id
from refresh_engine.contracts import ResourceSelectorABC
from refresh_engine.errors import ConfigurationError
from refresh_engine.models import Resource

__all__ = ["IDSelector", "PredicateSelector", "TagSelector"]


@dataclass(frozen=True, slots=True, init=False)
class IDSelector(ResourceSelectorABC):
    """Select resources whose ID is in a fixed set.

    Args:
        resource_ids: IDs to select. A single string is treated as one ID.

    Raises:
        ConfigurationError: If no ID is given or an ID is not a non-empty string.

    Attributes:
        resource_ids: The selected IDs.
    """

    resource_ids: frozenset[str]

    def __init__(self, resource_ids: str | Iterable[str]) -> None:
        """Create a selector for `resource_ids`."""
        ids = id_set(resource_ids, "resource_ids")
        if not ids:
            raise ConfigurationError("IDSelector requires at least one resource ID")
        object.__setattr__(self, "resource_ids", ids)

    def matches(self, resource: Resource, /) -> bool:
        """Return whether the resource ID is selected."""
        return resource.resource_id in self.resource_ids


@dataclass(frozen=True, slots=True, init=False)
class TagSelector(ResourceSelectorABC):
    """Select resources by the tags stored in their metadata.

    A resource's tags are read from `metadata[key]`, which may hold a single string
    or an iterable of strings.

    Args:
        tags: Tags to look for. A single string is treated as one tag.
        match_all: Require every tag when `True`; any tag when `False`.
        key: Metadata key that holds the resource's tags.

    Raises:
        ConfigurationError: If no tag is given or a tag is not a non-empty string.

    Attributes:
        tags: The tags to look for.
        match_all: Whether every tag is required.
        key: Metadata key that holds the resource's tags.
    """

    tags: frozenset[str]
    match_all: bool
    key: str

    def __init__(
        self,
        tags: str | Iterable[str],
        *,
        match_all: bool = True,
        key: str = "tags",
    ) -> None:
        """Create a selector for `tags`."""
        selected = id_set(tags, "tags")
        if not selected:
            raise ConfigurationError("TagSelector requires at least one tag")
        object.__setattr__(self, "tags", selected)
        object.__setattr__(self, "match_all", match_all)
        object.__setattr__(self, "key", require_id(key, "key"))

    def matches(self, resource: Resource, /) -> bool:
        """Return whether the resource carries the required tags."""
        raw = resource.metadata.get(self.key)
        if isinstance(raw, str):
            present = frozenset((raw,))
        elif isinstance(raw, Iterable):
            present = frozenset(raw)
        else:
            present = frozenset()
        if self.match_all:
            return self.tags <= present
        return not self.tags.isdisjoint(present)


@dataclass(frozen=True, slots=True)
class PredicateSelector(ResourceSelectorABC):
    """Select resources for which a function returns a truthy value.

    Attributes:
        predicate: Function called with each resource.

    Raises:
        TypeError: If `predicate` is not callable.
    """

    predicate: Callable[[Resource], object]

    def __post_init__(self) -> None:
        """Validate the predicate."""
        if not callable(self.predicate):
            raise TypeError("predicate must be callable")

    def matches(self, resource: Resource, /) -> bool:
        """Return whether the predicate accepts the resource."""
        return bool(self.predicate(resource))
