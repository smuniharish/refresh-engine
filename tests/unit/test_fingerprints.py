"""Tests for canonical hashing and fingerprint strategies."""

from __future__ import annotations

import dataclasses
import math
import re
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, NamedTuple, overload
from uuid import UUID

import pytest

from refresh_engine import (
    CompositeHash,
    ContentHash,
    ETagFingerprint,
    FingerprintError,
    FingerprintStrategy,
    FingerprintStrategyABC,
    MetadataHash,
    ResourceFingerprint,
    ResourceSnapshot,
    VersionTokenFingerprint,
    canonical_digest,
)


def exactly(message: str) -> str:
    return f"^{re.escape(message)}$"


class Color(Enum):
    RED = 1


class Paint(Enum):
    RED = 1


class Level(IntEnum):
    LOW = 1


class Speed(StrEnum):
    FAST = "fast"


class Broken(Enum):
    COMPLEX = 1j


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Vector:
    x: int
    y: int


@dataclass
class Holder:
    value: object


class Opaque:
    pass


class Name(str):
    pass


class Count(int):
    pass


class Ratio(float):
    pass


class Blob(bytes):
    pass


class Buffer(bytearray):
    pass


class Stamp(datetime):
    pass


class Day(date):
    pass


class Clock(time):
    pass


class Duration(timedelta):
    pass


class Money(Decimal):
    pass


class Identifier(UUID):
    pass


class Items(list[int]):
    pass


class Tags(frozenset[str]):
    pass


class Row(NamedTuple):
    x: int
    y: int


class Pairs(Mapping[str, int]):
    """A custom mapping that iterates in the order its items were given."""

    def __init__(self, *items: tuple[str, int]) -> None:
        self._items = dict(items)

    def __getitem__(self, key: str) -> int:
        return self._items[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


class OrderedSet(AbstractSet[int]):
    """A custom set that iterates in the order its items were given."""

    def __init__(self, *items: int) -> None:
        self._items = tuple(dict.fromkeys(items))

    def __contains__(self, item: object) -> bool:
        return item in self._items

    def __iter__(self) -> Iterator[int]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


class Span(Sequence[int]):
    """A custom read-only sequence."""

    def __init__(self, *items: int) -> None:
        self._items = items

    @overload
    def __getitem__(self, index: int) -> int: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[int, ...]: ...

    def __getitem__(self, index: int | slice) -> int | tuple[int, ...]:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)


# canonical_digest


def test_canonical_digest_returns_a_lowercase_sha256_hex_digest() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", canonical_digest({"key": [1, "two"]}))


def test_canonical_digest_is_stable_across_releases() -> None:
    # Persisted fingerprints depend on this value; changing the encoding forces
    # every stored resource to be refreshed again.
    value = {
        "name": "alpha",
        "tags": ["x", "y"],
        "size": 3,
        "ratio": 0.5,
        "flag": True,
        "missing": None,
        "day": date(2024, 1, 2),
        "price": Decimal("9.99"),
        "id": UUID(int=1),
    }

    assert (
        canonical_digest(value)
        == "3eeb3d5dde48332f8ad73361f46d558390640d193339f7b999e3211056f453e9"
    )


def test_canonical_digest_is_deterministic_for_equal_values() -> None:
    def build() -> object:
        return {
            "content": [1, 2.5, "three", b"four", None, True],
            "nested": {"set": {3, 1, 2}, "when": datetime(2024, 1, 1, tzinfo=UTC)},
        }

    assert canonical_digest(build()) == canonical_digest(build())


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param([1, "a"], (1, "a"), id="list-vs-tuple"),
        pytest.param(b"abc", bytearray(b"abc"), id="bytes-vs-bytearray"),
        pytest.param(b"abc", memoryview(b"abc"), id="bytes-vs-memoryview"),
        pytest.param({"a": 1, "b": 2}, {"b": 2, "a": 1}, id="mapping-order"),
        pytest.param(
            {"x": {"a": 1, "b": 2}}, {"x": {"b": 2, "a": 1}}, id="nested-mapping-order"
        ),
        pytest.param({1: "a", "1": "b"}, {"1": "b", 1: "a"}, id="mixed-key-order"),
        pytest.param({"a": 1}, MappingProxyType({"a": 1}), id="dict-vs-mappingproxy"),
        pytest.param({1, "1"}, frozenset({"1", 1}), id="set-vs-frozenset"),
        pytest.param(OrderedSet(1, 2, 3), OrderedSet(3, 2, 1), id="set-order"),
        pytest.param(timedelta(days=1), timedelta(hours=24), id="timedelta"),
        pytest.param(
            UUID(int=1), UUID("00000000-0000-0000-0000-000000000001"), id="uuid-forms"
        ),
        pytest.param(
            PureWindowsPath("dir\\file.txt"),
            PurePosixPath("dir/file.txt"),
            id="windows-vs-posix-path",
        ),
        pytest.param(Color.RED, Color(1), id="enum-member"),
        pytest.param(Point(1, 2), Point(1, 2), id="dataclass"),
        pytest.param("\ud800", "\ud800", id="lone-surrogate"),
    ],
)
def test_canonical_digest_is_equal_for_equivalent_values(
    first: object, second: object
) -> None:
    assert canonical_digest(first) == canonical_digest(second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param(True, 1, id="bool-vs-int"),
        pytest.param(False, 0, id="false-vs-zero"),
        pytest.param(None, False, id="none-vs-false"),
        pytest.param(1, 1.0, id="int-vs-float"),
        pytest.param(0.0, -0.0, id="zero-vs-negative-zero"),
        pytest.param("a", b"a", id="str-vs-bytes"),
        pytest.param("", b"", id="empty-str-vs-empty-bytes"),
        pytest.param("\ud800", "\ud801", id="lone-surrogates"),
        pytest.param([1, 2], [2, 1], id="sequence-order"),
        pytest.param(["ab", "c"], ["a", "bc"], id="sequence-boundaries"),
        pytest.param([["a"], "b"], ["a", ["b"]], id="sequence-nesting"),
        pytest.param({"a": "bc"}, {"ab": "c"}, id="mapping-boundaries"),
        pytest.param({"a": 1}, {"a": 2}, id="mapping-values"),
        pytest.param({"1": "a"}, {1: "a"}, id="mapping-key-types"),
        pytest.param({"a": 1}, [("a", 1)], id="mapping-vs-pairs"),
        pytest.param([], {}, id="empty-list-vs-empty-mapping"),
        pytest.param({1}, [1], id="set-vs-list"),
        pytest.param(set(), [], id="empty-set-vs-empty-list"),
        pytest.param(datetime(2024, 1, 1), date(2024, 1, 1), id="datetime-vs-date"),
        pytest.param(
            datetime(2024, 1, 1),
            datetime(2024, 1, 1, tzinfo=UTC),
            id="naive-vs-aware-datetime",
        ),
        pytest.param(time(12, 30), "12:30:00", id="time-vs-str"),
        pytest.param(time(12, 30), time(12, 31), id="time-values"),
        pytest.param(timedelta(seconds=1), timedelta(microseconds=1), id="timedelta"),
        pytest.param(timedelta(0), 0, id="timedelta-vs-int"),
        pytest.param(Decimal("1.5"), Decimal("2.5"), id="decimal-values"),
        pytest.param(Decimal("1.5"), "1.5", id="decimal-vs-str"),
        pytest.param(Decimal("1.5"), 1.5, id="decimal-vs-float"),
        pytest.param(UUID(int=1), UUID(int=2), id="uuid-values"),
        pytest.param(UUID(int=1), UUID(int=1).bytes, id="uuid-vs-bytes"),
        pytest.param(UUID(int=1), str(UUID(int=1)), id="uuid-vs-str"),
        pytest.param(PurePosixPath("a/b"), "a/b", id="path-vs-str"),
        pytest.param(PurePosixPath("a/b"), PurePosixPath("a/c"), id="path-values"),
        pytest.param(Color.RED, 1, id="enum-vs-value"),
        pytest.param(Color.RED, Paint.RED, id="enum-types"),
        pytest.param(Level.LOW, 1, id="intenum-vs-int"),
        pytest.param(Speed.FAST, "fast", id="strenum-vs-str"),
        pytest.param(Level.LOW, Color.RED, id="intenum-vs-enum"),
        pytest.param(Point(1, 2), Vector(1, 2), id="dataclass-types"),
        pytest.param(Point(1, 2), Point(2, 1), id="dataclass-fields"),
        pytest.param(Point(1, 2), (1, 2), id="dataclass-vs-tuple"),
    ],
)
def test_canonical_digest_distinguishes_different_values(
    first: object, second: object
) -> None:
    assert canonical_digest(first) != canonical_digest(second)


def test_canonical_digest_distinguishes_integers_by_value_and_sign() -> None:
    values = [0, 1, -1, 127, 128, -128, -129, 255, 256, -256, 2**63, -(2**63), 2**64]

    assert len({canonical_digest(value) for value in values}) == len(values)


def test_canonical_digest_supports_integers_beyond_the_string_conversion_limit() -> (
    None
):
    huge = 10**5000

    assert canonical_digest(huge) == canonical_digest(10**5000)
    assert canonical_digest(huge) != canonical_digest(huge + 1)
    assert canonical_digest(-huge) != canonical_digest(huge)


def test_canonical_digest_distinguishes_floats_by_bit_pattern() -> None:
    values = [0.0, -0.0, 1.0, -1.0, 0.1, 5e-324, 1.7976931348623157e308]

    assert len({canonical_digest(value) for value in values}) == len(values)


@pytest.mark.parametrize(
    "value",
    [math.inf, -math.inf, math.nan, [1.0, math.nan], {"ratio": math.inf}],
    ids=["inf", "-inf", "nan", "nested-nan", "nested-inf"],
)
def test_canonical_digest_rejects_non_finite_floats(value: object) -> None:
    with pytest.raises(
        FingerprintError, match=exactly("non-finite floats cannot be fingerprinted")
    ):
        canonical_digest(value)


@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity")],
    ids=["nan", "snan", "inf", "-inf"],
)
def test_canonical_digest_rejects_non_finite_decimals(value: Decimal) -> None:
    with pytest.raises(
        FingerprintError, match=exactly("non-finite decimals cannot be fingerprinted")
    ):
        canonical_digest(value)


def test_canonical_digest_supports_negative_timedeltas() -> None:
    assert canonical_digest(timedelta(microseconds=-1)) != canonical_digest(
        timedelta(microseconds=1)
    )


@pytest.mark.parametrize(
    ("value", "base"),
    [
        pytest.param(Name("alpha"), "alpha", id="str"),
        pytest.param(Count(7), 7, id="int"),
        pytest.param(Ratio(0.5), 0.5, id="float"),
        pytest.param(Blob(b"raw"), b"raw", id="bytes"),
        pytest.param(Buffer(b"raw"), b"raw", id="bytearray"),
        pytest.param(
            Stamp(2024, 1, 2, 3, 4, 5), datetime(2024, 1, 2, 3, 4, 5), id="datetime"
        ),
        pytest.param(Day(2024, 1, 2), date(2024, 1, 2), id="date"),
        pytest.param(Clock(12, 30), time(12, 30), id="time"),
        pytest.param(Duration(seconds=5), timedelta(seconds=5), id="timedelta"),
        pytest.param(Money("9.99"), Decimal("9.99"), id="decimal"),
        pytest.param(Identifier(int=5), UUID(int=5), id="uuid"),
        pytest.param(
            OrderedDict([("b", 2), ("a", 1)]), {"a": 1, "b": 2}, id="ordereddict"
        ),
        pytest.param(defaultdict(int, {"a": 1}), {"a": 1}, id="defaultdict"),
        pytest.param(Counter("aab"), {"a": 2, "b": 1}, id="counter"),
        pytest.param(Pairs(("b", 2), ("a", 1)), {"a": 1, "b": 2}, id="custom-mapping"),
        pytest.param(OrderedSet(3, 1, 2), {1, 2, 3}, id="custom-set"),
        pytest.param(Tags({"x", "y"}), frozenset({"y", "x"}), id="frozenset"),
        pytest.param(Span(1, 2, 3), [1, 2, 3], id="custom-sequence"),
        pytest.param(Items([1, 2]), [1, 2], id="list"),
        pytest.param(Row(1, 2), (1, 2), id="namedtuple"),
    ],
)
def test_subclasses_of_supported_types_encode_like_their_base(
    value: object, base: object
) -> None:
    assert canonical_digest(value) == canonical_digest(base)


@pytest.mark.parametrize(
    ("value", "type_name"),
    [
        pytest.param(object(), "builtins.object", id="object"),
        pytest.param(1j, "builtins.complex", id="complex"),
        pytest.param(Opaque(), f"{Opaque.__module__}.Opaque", id="custom-class"),
        pytest.param({"key": [Opaque()]}, f"{Opaque.__module__}.Opaque", id="nested"),
        pytest.param(Holder(value=1j), "builtins.complex", id="dataclass-field"),
        pytest.param(Broken.COMPLEX, "builtins.complex", id="enum-value"),
        pytest.param(Point, "builtins.type", id="dataclass-type"),
    ],
)
def test_canonical_digest_rejects_unsupported_types_by_name(
    value: object, type_name: str
) -> None:
    with pytest.raises(
        FingerprintError,
        match=exactly(f"unsupported fingerprint value type: {type_name}"),
    ):
        canonical_digest(value)


def test_canonical_digest_rejects_a_self_referential_list() -> None:
    value: list[object] = []
    value.append(value)

    with pytest.raises(
        FingerprintError,
        match=exactly("value is nested too deeply or contains a reference cycle"),
    ) as caught:
        canonical_digest(value)

    assert caught.value.__suppress_context__ is True


def test_canonical_digest_rejects_a_self_referential_mapping() -> None:
    value: dict[str, object] = {}
    value["self"] = value

    with pytest.raises(FingerprintError, match="reference cycle"):
        canonical_digest(value)


def test_canonical_digest_rejects_values_nested_too_deeply() -> None:
    value: list[object] = []
    for _ in range(100_000):
        value = [value]

    with pytest.raises(FingerprintError, match="nested too deeply"):
        canonical_digest(value)


# Strategies

REVISION = "rev-1"
OTHER_REVISION = "rev-2"
SNAPSHOT = ResourceSnapshot(
    "alpha",
    content={"title": "Hello", "body": [1, 2]},
    metadata={"size": 2},
    version="v1",
    version_token=REVISION,
    etag="etag-1",
    dependencies={"beta"},
)

STRATEGIES: list[FingerprintStrategyABC] = [
    CompositeHash(),
    ContentHash(),
    MetadataHash(),
    VersionTokenFingerprint(),
    ETagFingerprint(),
]


@pytest.mark.parametrize(
    "strategy", STRATEGIES, ids=lambda strategy: type(strategy).__name__
)
def test_strategies_implement_both_fingerprint_contracts(
    strategy: FingerprintStrategyABC,
) -> None:
    assert isinstance(strategy, FingerprintStrategyABC)
    assert isinstance(strategy, FingerprintStrategy)


@pytest.mark.parametrize(
    ("strategy", "algorithm"),
    [
        (CompositeHash(), "sha256:composite"),
        (ContentHash(), "sha256:content"),
        (MetadataHash(), "sha256:metadata"),
        (VersionTokenFingerprint(), "version-token"),
        (ETagFingerprint(), "etag"),
    ],
    ids=lambda value: type(value).__name__ if not isinstance(value, str) else value,
)
async def test_strategies_name_their_algorithm(
    strategy: FingerprintStrategyABC, algorithm: str
) -> None:
    fingerprint = await strategy.fingerprint(SNAPSHOT)

    assert fingerprint.algorithm == algorithm


@pytest.mark.parametrize(
    "strategy", STRATEGIES, ids=lambda strategy: type(strategy).__name__
)
async def test_strategies_are_deterministic(strategy: FingerprintStrategyABC) -> None:
    copy = dataclasses.replace(SNAPSHOT, content={"body": [1, 2], "title": "Hello"})

    assert await strategy.fingerprint(SNAPSHOT) == await strategy.fingerprint(copy)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content", {"title": "Bye", "body": [1, 2]}),
        ("metadata", {"size": 3}),
        ("version", "v2"),
        ("version", None),
        ("version_token", OTHER_REVISION),
        ("etag", "etag-2"),
        ("etag", None),
    ],
)
async def test_composite_hash_changes_with_every_fingerprinted_field(
    field: str, value: object
) -> None:
    options: dict[str, Any] = {field: value}
    changed = dataclasses.replace(SNAPSHOT, **options)
    strategy = CompositeHash()

    assert await strategy.fingerprint(changed) != await strategy.fingerprint(SNAPSHOT)


@pytest.mark.parametrize("dependencies", [{"gamma"}, None], ids=["changed", "unknown"])
async def test_composite_hash_ignores_dependencies(
    dependencies: set[str] | None,
) -> None:
    changed = dataclasses.replace(SNAPSHOT, dependencies=dependencies)
    strategy = CompositeHash()

    assert await strategy.fingerprint(changed) == await strategy.fingerprint(SNAPSHOT)


async def test_composite_hash_rejects_unsupported_content() -> None:
    snapshot = ResourceSnapshot("alpha", content=Opaque())

    with pytest.raises(FingerprintError, match="unsupported fingerprint value type"):
        await CompositeHash().fingerprint(snapshot)


async def test_content_hash_digests_only_the_content() -> None:
    other = dataclasses.replace(
        SNAPSHOT,
        metadata={"size": 99},
        version="v9",
        version_token=OTHER_REVISION,
        etag="e",
    )

    fingerprint = await ContentHash().fingerprint(SNAPSHOT)

    assert fingerprint == ResourceFingerprint(
        "sha256:content", canonical_digest({"title": "Hello", "body": [1, 2]})
    )
    assert await ContentHash().fingerprint(other) == fingerprint


async def test_content_hash_changes_with_the_content() -> None:
    changed = dataclasses.replace(SNAPSHOT, content={"title": "Bye"})
    strategy = ContentHash()

    assert await strategy.fingerprint(changed) != await strategy.fingerprint(SNAPSHOT)


async def test_metadata_hash_digests_only_the_metadata() -> None:
    other = dataclasses.replace(SNAPSHOT, content="different", version="v9")

    fingerprint = await MetadataHash().fingerprint(SNAPSHOT)

    assert fingerprint == ResourceFingerprint(
        "sha256:metadata", canonical_digest({"size": 2})
    )
    assert await MetadataHash().fingerprint(other) == fingerprint


async def test_metadata_hash_changes_with_the_metadata() -> None:
    changed = dataclasses.replace(SNAPSHOT, metadata={"size": 3})
    strategy = MetadataHash()

    assert await strategy.fingerprint(changed) != await strategy.fingerprint(SNAPSHOT)


async def test_version_token_fingerprint_uses_the_token() -> None:
    fingerprint = await VersionTokenFingerprint().fingerprint(SNAPSHOT)

    assert fingerprint == ResourceFingerprint("version-token", REVISION)


@pytest.mark.parametrize("token", [None, ""], ids=["missing", "empty"])
async def test_version_token_fingerprint_requires_a_token(token: str | None) -> None:
    snapshot = dataclasses.replace(SNAPSHOT, version_token=token)

    with pytest.raises(
        FingerprintError, match=exactly("the snapshot has no version_token")
    ):
        await VersionTokenFingerprint().fingerprint(snapshot)


async def test_etag_fingerprint_uses_the_etag() -> None:
    fingerprint = await ETagFingerprint().fingerprint(SNAPSHOT)

    assert fingerprint == ResourceFingerprint("etag", "etag-1")


@pytest.mark.parametrize("etag", [None, ""], ids=["missing", "empty"])
async def test_etag_fingerprint_requires_an_etag(etag: str | None) -> None:
    snapshot = dataclasses.replace(SNAPSHOT, etag=etag)

    with pytest.raises(FingerprintError, match=exactly("the snapshot has no etag")):
        await ETagFingerprint().fingerprint(snapshot)
