"""Tests for the private validation helpers in `refresh_engine._internal.values`."""

from __future__ import annotations

import copy
import json
import math
import pickle
import re
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, cast

import pytest

from refresh_engine import ConfigurationError
from refresh_engine._internal.values import (
    EMPTY_MAPPING,
    FrozenDict,
    FrozenList,
    freeze_mapping,
    id_set,
    number,
    optional_text,
    optional_timeout,
    positive_int,
    require_id,
    strict_bool,
    text_tuple,
    utc_now,
)


def exactly(message: str) -> str:
    return f"^{re.escape(message)}$"


def test_empty_mapping_is_empty_and_read_only() -> None:
    assert EMPTY_MAPPING == {}
    with pytest.raises(TypeError):
        cast(Any, EMPTY_MAPPING)["key"] = "value"


def test_utc_now_returns_the_current_aware_utc_time() -> None:
    before = datetime.now(UTC)
    value = utc_now()
    after = datetime.now(UTC)

    assert value.tzinfo is UTC
    assert value.utcoffset() == timedelta(0)
    assert before <= value <= after


@pytest.mark.parametrize(
    "value",
    [None, {}, MappingProxyType({})],
    ids=["none", "empty-dict", "empty-proxy"],
)
def test_freeze_mapping_returns_the_shared_empty_mapping_for_empty_input(
    value: dict[str, Any] | None,
) -> None:
    assert freeze_mapping(value) is EMPTY_MAPPING


def test_freeze_mapping_returns_an_independent_read_only_copy() -> None:
    source: dict[str, Any] = {"key": "value"}

    frozen = freeze_mapping(source)
    source["key"] = "changed"
    source["other"] = 1

    assert frozen == {"key": "value"}
    with pytest.raises(TypeError):
        cast(Any, frozen)["key"] = "changed"


def test_freeze_mapping_freezes_nested_builtin_containers() -> None:
    source: dict[str, Any] = {
        "settings": {"enabled": True, "levels": [1, {"deep": [2]}]},
        "tags": {"a", "b"},
        "pair": ([1], "x"),
        "view": MappingProxyType({"k": [3]}),
    }

    frozen = freeze_mapping(source)
    source["settings"]["enabled"] = False
    source["settings"]["levels"].append(9)
    source["tags"].add("c")
    source["pair"][0].append(2)

    assert frozen == {
        "settings": {"enabled": True, "levels": [1, {"deep": [2]}]},
        "tags": frozenset({"a", "b"}),
        "pair": ([1], "x"),
        "view": {"k": [3]},
    }
    settings = frozen["settings"]
    assert isinstance(settings, FrozenDict)
    assert isinstance(settings["levels"], FrozenList)
    assert isinstance(frozen["tags"], frozenset)
    assert isinstance(frozen["pair"][0], FrozenList)
    assert isinstance(frozen["view"], FrozenDict)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.__setitem__("x", 1),
        lambda d: d.__delitem__("enabled"),
        lambda d: d.clear(),
        lambda d: d.pop("enabled"),
        lambda d: d.popitem(),
        lambda d: d.setdefault("x", 1),
        lambda d: d.update(x=1),
        lambda d: d.__ior__({"x": 1}),
    ],
)
def test_frozen_dict_rejects_every_mutator(mutate: Any) -> None:
    nested = freeze_mapping({"settings": {"enabled": True}})["settings"]

    with pytest.raises(TypeError, match="FrozenDict is immutable"):
        mutate(nested)
    assert nested == {"enabled": True}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.__setitem__(0, 9),
        lambda v: v.__delitem__(0),
        lambda v: v.__iadd__([9]),
        lambda v: v.__imul__(2),
        lambda v: v.append(9),
        lambda v: v.extend([9]),
        lambda v: v.insert(0, 9),
        lambda v: v.pop(),
        lambda v: v.remove(1),
        lambda v: v.clear(),
        lambda v: v.sort(),
        lambda v: v.reverse(),
    ],
)
def test_frozen_list_rejects_every_mutator(mutate: Any) -> None:
    nested = freeze_mapping({"items": [2, 1]})["items"]

    with pytest.raises(TypeError, match="FrozenList is immutable"):
        mutate(nested)
    assert nested == [2, 1]


def test_frozen_containers_serialize_pickle_and_copy_like_builtins() -> None:
    frozen = freeze_mapping({"settings": {"items": [1, 2]}})

    assert json.loads(json.dumps(dict(frozen))) == {"settings": {"items": [1, 2]}}
    restored = pickle.loads(pickle.dumps(frozen["settings"]))  # noqa: S301
    assert restored == {"items": [1, 2]}
    assert isinstance(restored, FrozenDict)
    assert isinstance(restored["items"], FrozenList)
    assert copy.deepcopy(frozen["settings"]) == {"items": [1, 2]}


def test_freeze_mapping_reuses_frozen_values_and_shared_references() -> None:
    shared = [1]
    frozen = freeze_mapping({"a": shared, "b": shared})
    refrozen = freeze_mapping(frozen)

    assert frozen["a"] is frozen["b"]
    assert refrozen["a"] is frozen["a"]


def test_freeze_mapping_keeps_arbitrary_objects_and_subclasses_by_reference() -> None:
    class Payload:
        pass

    payload = Payload()
    ordered = OrderedDict(a=[1])

    frozen = freeze_mapping({"payload": payload, "ordered": ordered})

    assert frozen["payload"] is payload
    assert frozen["ordered"] is ordered


def test_freeze_mapping_rejects_reference_cycles() -> None:
    cycle: list[Any] = []
    cycle.append(cycle)

    with pytest.raises(
        ConfigurationError, match=exactly("metadata must not contain reference cycles")
    ):
        freeze_mapping({"cycle": cycle})


def test_freeze_mapping_rejects_excessive_nesting() -> None:
    deep: list[Any] = []
    for _ in range(100_000):
        deep = [deep]

    with pytest.raises(
        ConfigurationError, match=exactly("attributes is nested too deeply")
    ):
        freeze_mapping({"deep": deep}, "attributes")


@pytest.mark.parametrize("value", [True, False])
def test_strict_bool_accepts_booleans(value: bool) -> None:
    assert strict_bool(value, "complete") is value


@pytest.mark.parametrize(
    ("value", "kind"),
    [("true", "str"), ("false", "str"), (0, "int"), (1, "int"), (None, "NoneType")],
)
def test_strict_bool_rejects_values_that_are_not_booleans(
    value: object, kind: str
) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly(f"complete must be a bool, not {kind}")
    ):
        strict_bool(value, "complete")


def test_require_id_returns_a_non_empty_string() -> None:
    assert require_id("resource-1", "resource_id") == "resource-1"


@pytest.mark.parametrize("value", ["", None, 1, b"id", ["id"]])
def test_require_id_rejects_values_that_are_not_non_empty_strings(
    value: object,
) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("resource_id must be a non-empty string")
    ):
        require_id(value, "resource_id")


@pytest.mark.parametrize("value", [None, "", "text"])
def test_optional_text_accepts_none_and_strings(value: str | None) -> None:
    assert optional_text(value, "version") == value


@pytest.mark.parametrize("value", [1, b"text", ["text"], 1.5])
def test_optional_text_rejects_other_values(value: object) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("version must be a string or None")
    ):
        optional_text(value, "version")


def test_id_set_treats_a_single_string_as_one_id() -> None:
    assert id_set("abc", "ids") == frozenset({"abc"})


def test_id_set_collects_an_iterable_into_a_frozenset() -> None:
    ids = id_set(iter(["a", "b", "a"]), "ids")

    assert ids == frozenset({"a", "b"})
    assert isinstance(ids, frozenset)


def test_id_set_accepts_an_empty_iterable() -> None:
    assert id_set([], "ids") == frozenset()


@pytest.mark.parametrize(
    "value",
    [[""], ["a", 1], ["a", None], [b"a"]],
    ids=["empty-string", "int", "none", "bytes"],
)
def test_id_set_rejects_items_that_are_not_non_empty_strings(value: list[Any]) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("ids must contain only non-empty strings")
    ):
        id_set(value, "ids")


def test_text_tuple_treats_a_single_string_as_one_item() -> None:
    assert text_tuple("note", "notes") == ("note",)


def test_text_tuple_keeps_the_order_of_an_iterable() -> None:
    assert text_tuple(iter(["b", "", "a"]), "notes") == ("b", "", "a")


@pytest.mark.parametrize("value", [["a", 1], [None], [b"a"]])
def test_text_tuple_rejects_non_string_items(value: list[Any]) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("notes must contain only strings")
    ):
        text_tuple(value, "notes")


@pytest.mark.parametrize(
    ("value", "minimum", "exclusive"),
    [
        (0, 0, False),
        (1, 1, False),
        (2.5, 0, False),
        (0.001, 0, True),
        (10**6, 1, True),
    ],
)
def test_number_returns_values_within_range_unchanged(
    value: float, minimum: float, exclusive: bool
) -> None:
    result = number(value, "delay", minimum=minimum, exclusive=exclusive)

    assert result == value
    assert type(result) is type(value)


@pytest.mark.parametrize(
    "value",
    [True, False, "1", None, 1j, math.inf, -math.inf, math.nan],
    ids=["true", "false", "str", "none", "complex", "inf", "-inf", "nan"],
)
def test_number_rejects_booleans_non_numbers_and_non_finite_values(
    value: object,
) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("delay must be a finite number")
    ):
        number(value, "delay", minimum=0)


@pytest.mark.parametrize(
    ("value", "minimum", "exclusive", "message"),
    [
        (-1, 0, False, "delay must be at least 0"),
        (-0.001, 0, False, "delay must be at least 0"),
        (0.5, 1, False, "delay must be at least 1"),
        (0.25, 0.5, False, "delay must be at least 0.5"),
        (0, 0, True, "delay must be greater than 0"),
        (-1, 0, True, "delay must be greater than 0"),
        (1, 1, True, "delay must be greater than 1"),
    ],
)
def test_number_rejects_values_below_the_minimum(
    value: float, minimum: float, exclusive: bool, message: str
) -> None:
    with pytest.raises(ConfigurationError, match=exactly(message)):
        number(value, "delay", minimum=minimum, exclusive=exclusive)


def test_optional_timeout_accepts_none() -> None:
    assert optional_timeout(None, "timeout") is None


def test_optional_timeout_accepts_positive_numbers() -> None:
    assert optional_timeout(0.25, "timeout") == 0.25


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (0, "timeout must be greater than 0"),
        (-1, "timeout must be greater than 0"),
        (math.inf, "timeout must be a finite number"),
        (True, "timeout must be a finite number"),
    ],
)
def test_optional_timeout_rejects_non_positive_or_non_finite_values(
    value: object, message: str
) -> None:
    with pytest.raises(ConfigurationError, match=exactly(message)):
        optional_timeout(value, "timeout")


@pytest.mark.parametrize("value", [1, 2, 10**20])
def test_positive_int_accepts_integers_of_at_least_one(value: int) -> None:
    assert positive_int(value, "count") == value


@pytest.mark.parametrize(
    "value",
    [0, -1, True, False, 1.0, "1", None],
    ids=["zero", "negative", "true", "false", "float", "str", "none"],
)
def test_positive_int_rejects_other_values(value: object) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("count must be an integer of at least 1")
    ):
        positive_int(value, "count")
