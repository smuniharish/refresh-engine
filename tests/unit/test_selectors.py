"""Tests for the reusable selectors in `refresh_engine.selectors`."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, cast

import pytest

from refresh_engine import (
    ConfigurationError,
    IDSelector,
    PredicateSelector,
    Resource,
    ResourceSelector,
    ResourceSelectorABC,
    TagSelector,
)


def exactly(message: str) -> str:
    return f"^{re.escape(message)}$"


def tagged(tags: object, *, key: str = "tags") -> Resource:
    return Resource("tagged", metadata={key: tags})


# IDSelector


def test_id_selector_matches_only_selected_ids() -> None:
    selector = IDSelector(["alpha", "beta"])

    assert selector.matches(Resource("alpha")) is True
    assert selector.matches(Resource("beta")) is True
    assert selector.matches(Resource("gamma")) is False


def test_id_selector_treats_a_single_string_as_one_id() -> None:
    selector = IDSelector("alpha")

    assert selector.resource_ids == frozenset({"alpha"})
    assert selector.matches(Resource("alpha")) is True
    assert selector.matches(Resource("a")) is False


@pytest.mark.parametrize("resource_ids", [[], set(), ()], ids=["list", "set", "tuple"])
def test_id_selector_requires_at_least_one_id(resource_ids: object) -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly("IDSelector requires at least one resource ID"),
    ):
        IDSelector(cast(Any, resource_ids))


@pytest.mark.parametrize(
    "resource_ids",
    [[""], "", ["alpha", 1], [None], [["alpha"]]],
    ids=["empty-string-item", "empty-string", "int", "none", "unhashable"],
)
def test_id_selector_rejects_ids_that_are_not_non_empty_strings(
    resource_ids: object,
) -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly("resource_ids must contain only non-empty strings"),
    ):
        IDSelector(cast(Any, resource_ids))


def test_id_selectors_compare_by_their_ids() -> None:
    first = IDSelector(["alpha", "beta"])
    second = IDSelector(("beta", "alpha", "alpha"))

    assert first == second
    assert hash(first) == hash(second)
    assert first != IDSelector("alpha")
    assert len({first, second}) == 1


def test_id_selector_repr_shows_the_ids() -> None:
    assert repr(IDSelector("alpha")) == "IDSelector(resource_ids=frozenset({'alpha'}))"


# TagSelector


def test_tag_selector_defaults() -> None:
    selector = TagSelector(["critical"])

    assert selector.tags == frozenset({"critical"})
    assert selector.match_all is True
    assert selector.key == "tags"


def test_tag_selector_treats_a_single_string_as_one_tag() -> None:
    selector = TagSelector("critical")

    assert selector.tags == frozenset({"critical"})
    assert selector.matches(tagged(["critical"])) is True
    assert selector.matches(tagged(["c", "r", "i", "t", "a", "l"])) is False


@pytest.mark.parametrize("tags", [[], set()], ids=["list", "set"])
def test_tag_selector_requires_at_least_one_tag(tags: object) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("TagSelector requires at least one tag")
    ):
        TagSelector(cast(Any, tags))


@pytest.mark.parametrize(
    "tags",
    ["", [""], ["critical", 1], [None], [["critical"]]],
    ids=["empty-string", "empty-string-item", "int", "none", "unhashable"],
)
def test_tag_selector_rejects_tags_that_are_not_non_empty_strings(
    tags: object,
) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("tags must contain only non-empty strings")
    ):
        TagSelector(cast(Any, tags))


@pytest.mark.parametrize("key", ["", None, 1])
def test_tag_selector_requires_a_non_empty_key(key: object) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("key must be a non-empty string")
    ):
        TagSelector("critical", key=cast(Any, key))


@pytest.mark.parametrize(
    ("resource_tags", "expected"),
    [
        (["critical", "public"], True),
        (["critical", "public", "beta"], True),
        (["critical"], False),
        ([], False),
    ],
)
def test_tag_selector_requires_every_tag_by_default(
    resource_tags: list[str], expected: bool
) -> None:
    selector = TagSelector(["critical", "public"])

    assert selector.matches(tagged(resource_tags)) is expected


@pytest.mark.parametrize(
    ("resource_tags", "expected"),
    [
        (["critical", "public"], True),
        (["public"], True),
        (["beta"], False),
        ([], False),
    ],
)
def test_tag_selector_requires_any_tag_when_match_all_is_false(
    resource_tags: list[str], expected: bool
) -> None:
    selector = TagSelector(["critical", "public"], match_all=False)

    assert selector.matches(tagged(resource_tags)) is expected


def test_tag_selector_reads_tags_from_a_custom_key() -> None:
    selector = TagSelector("critical", key="labels")

    assert selector.matches(tagged(["critical"], key="labels")) is True
    assert selector.matches(tagged(["critical"])) is False


@pytest.mark.parametrize(
    "resource_tags",
    [("critical", "beta"), {"critical"}, frozenset({"critical", "beta"})],
    ids=["tuple", "set", "frozenset"],
)
def test_tag_selector_accepts_any_iterable_of_resource_tags(
    resource_tags: object,
) -> None:
    assert TagSelector("critical").matches(tagged(resource_tags)) is True


def test_tag_selector_treats_a_string_resource_tag_as_one_tag() -> None:
    resource = tagged("critical")

    assert TagSelector("critical").matches(resource) is True
    assert TagSelector("c").matches(resource) is False
    assert TagSelector("c", match_all=False).matches(resource) is False


@pytest.mark.parametrize("match_all", [True, False])
@pytest.mark.parametrize(
    "resource",
    [Resource("untagged"), tagged(42), tagged(None)],
    ids=["missing", "non-iterable", "none"],
)
def test_resources_without_readable_tags_have_no_tags(
    resource: Resource, match_all: bool
) -> None:
    assert TagSelector("critical", match_all=match_all).matches(resource) is False


def test_tag_selectors_compare_by_their_fields() -> None:
    selector = TagSelector(["a", "b"])

    assert selector == TagSelector(("b", "a"))
    assert hash(selector) == hash(TagSelector(("b", "a")))
    assert selector != TagSelector(["a", "b"], match_all=False)
    assert selector != TagSelector(["a", "b"], key="labels")


def test_tag_selector_repr_shows_its_fields() -> None:
    assert (
        repr(TagSelector("critical", match_all=False, key="labels"))
        == "TagSelector(tags=frozenset({'critical'}), match_all=False, key='labels')"
    )


# PredicateSelector


def test_predicate_selector_passes_the_resource_to_the_predicate() -> None:
    seen: list[Resource] = []
    resource = Resource("alpha")

    def predicate(candidate: Resource) -> bool:
        seen.append(candidate)
        return True

    assert PredicateSelector(predicate).matches(resource) is True
    assert seen == [resource]


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [(1, True), ("yes", True), ([0], True), (0, False), ("", False), (None, False)],
)
def test_predicate_selector_uses_the_truthiness_of_the_result(
    outcome: object, expected: bool
) -> None:
    selector = PredicateSelector(lambda _: outcome)

    assert selector.matches(Resource("alpha")) is expected


@pytest.mark.parametrize("predicate", [None, "alpha", 1])
def test_predicate_selector_requires_a_callable(predicate: object) -> None:
    with pytest.raises(TypeError, match=exactly("predicate must be callable")):
        PredicateSelector(cast(Any, predicate))


def test_predicate_selectors_compare_by_predicate() -> None:
    def first(resource: Resource) -> bool:
        return True

    def second(resource: Resource) -> bool:
        return True

    assert PredicateSelector(first) == PredicateSelector(first)
    assert hash(PredicateSelector(first)) == hash(PredicateSelector(first))
    assert PredicateSelector(first) != PredicateSelector(second)
    assert repr(PredicateSelector(first)) == f"PredicateSelector(predicate={first!r})"


# Shared behavior

SELECTORS: list[ResourceSelectorABC] = [
    IDSelector("alpha"),
    TagSelector("critical"),
    PredicateSelector(lambda _: True),
]


@pytest.mark.parametrize(
    "selector", SELECTORS, ids=lambda selector: type(selector).__name__
)
def test_selectors_implement_both_selector_contracts(
    selector: ResourceSelectorABC,
) -> None:
    assert isinstance(selector, ResourceSelectorABC)
    assert isinstance(selector, ResourceSelector)


@pytest.mark.parametrize(
    "selector", SELECTORS, ids=lambda selector: type(selector).__name__
)
def test_selectors_are_frozen(selector: ResourceSelectorABC) -> None:
    field = dataclasses.fields(cast(Any, selector))[0].name

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(selector, field, None)


def test_selectors_compose_with_operators() -> None:
    critical = Resource("beta", metadata={"tags": ["critical"]})
    archived = Resource("gamma", metadata={"tags": ["critical", "archived"]})
    selector = (IDSelector("alpha") | TagSelector("critical")) & ~TagSelector(
        "archived"
    )

    assert selector.matches(Resource("alpha")) is True
    assert selector.matches(critical) is True
    assert selector.matches(archived) is False
    assert selector.matches(Resource("delta")) is False
