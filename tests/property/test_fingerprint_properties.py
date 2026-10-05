from __future__ import annotations

import struct
from collections.abc import Hashable
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from refresh_engine import canonical_digest

scalars = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text()
    | st.binary()
)
hashables = st.none() | st.booleans() | st.integers() | st.text() | st.binary()
values = st.recursive(
    scalars,
    lambda children: (
        st.lists(children, max_size=4)
        | st.lists(children, max_size=4).map(tuple)
        | st.dictionaries(hashables, children, max_size=4)
        | st.frozensets(hashables, max_size=4)
    ),
    max_leaves=12,
)


def reference(value: Any) -> Hashable:
    """Independent model of canonical equality."""
    if value is None:
        return ("none",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", struct.pack(">d", value))
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, bytes | bytearray):
        return ("bytes", bytes(value))
    if isinstance(value, list | tuple):
        return ("sequence", tuple(reference(item) for item in value))
    if isinstance(value, dict):
        return (
            "mapping",
            frozenset((reference(key), reference(item)) for key, item in value.items()),
        )
    if isinstance(value, frozenset | set):
        return ("set", frozenset(reference(item) for item in value))
    raise AssertionError(f"unexpected value {value!r}")


@given(values)
def test_digest_is_deterministic(value: Any) -> None:
    assert canonical_digest(value) == canonical_digest(value)


@given(values, values)
def test_digests_are_equal_exactly_for_canonically_equal_values(
    first: Any, second: Any
) -> None:
    same = reference(first) == reference(second)
    assert (canonical_digest(first) == canonical_digest(second)) is same


@given(st.dictionaries(hashables, values, max_size=6))
def test_mapping_digest_ignores_insertion_order(mapping: dict[Any, Any]) -> None:
    reordered = dict(reversed(list(mapping.items())))
    assert canonical_digest(mapping) == canonical_digest(reordered)


@given(st.lists(hashables, max_size=8))
def test_set_digest_ignores_construction_order(items: list[Any]) -> None:
    members = list(frozenset(items))
    digest = canonical_digest(frozenset(members))
    assert canonical_digest(frozenset(reversed(members))) == digest
    assert canonical_digest(set(members)) == digest


@given(st.binary())
def test_binary_types_with_equal_content_share_a_digest(data: bytes) -> None:
    digest = canonical_digest(data)
    assert canonical_digest(bytearray(data)) == digest
    assert canonical_digest(memoryview(data)) == digest


@given(st.lists(values, max_size=5))
def test_lists_and_tuples_with_equal_items_share_a_digest(items: list[Any]) -> None:
    assert canonical_digest(items) == canonical_digest(tuple(items))


@given(st.integers(min_value=-(2**53), max_value=2**53))
def test_numbers_strings_and_booleans_never_collide(number: int) -> None:
    digests = {
        canonical_digest(number),
        canonical_digest(str(number)),
        canonical_digest(float(number)),
    }
    assert len(digests) == 3
    assert canonical_digest(number == 1) not in digests


@given(st.text(min_size=1), st.text(min_size=1))
def test_sequence_boundaries_are_unambiguous(left: str, right: str) -> None:
    assert canonical_digest([left + right]) != canonical_digest([left, right])
