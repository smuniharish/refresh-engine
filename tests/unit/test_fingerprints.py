from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import PurePath

import pytest
from hypothesis import given
from hypothesis import strategies as st

from refresh_engine import (
    CompositeHash,
    ContentHash,
    ETagFingerprint,
    MetadataHash,
    ResourceSnapshot,
    VersionTokenFingerprint,
)
from refresh_engine.detection import canonical_bytes
from refresh_engine.errors import FingerprintError


@given(st.dictionaries(st.text(), st.integers() | st.none()))
def test_canonical_mapping_is_independent_of_insertion_order(
    value: dict[str, int | None],
) -> None:
    assert canonical_bytes(value) == canonical_bytes(dict(reversed(value.items())))


def test_canonical_encoding_distinguishes_types() -> None:
    assert canonical_bytes(1) != canonical_bytes("1")
    assert canonical_bytes([1]) != canonical_bytes({1})


@pytest.mark.asyncio
async def test_hash_strategies_are_deterministic() -> None:
    snapshot = ResourceSnapshot("r", content={"b": 2, "a": 1})
    content = ContentHash()
    composite = CompositeHash()
    assert await content.fingerprint(snapshot) == await content.fingerprint(snapshot)
    assert await composite.fingerprint(snapshot) == await composite.fingerprint(
        snapshot
    )


def test_unsupported_values_raise_structured_error() -> None:
    with pytest.raises(FingerprintError):
        canonical_bytes(object())


def test_canonical_encoding_supports_common_structured_types() -> None:
    class Choice(Enum):
        A = "a"

    @dataclass
    class Value:
        item: int

    encoded = canonical_bytes(
        {
            "bytes": b"value",
            "date": datetime(2026, 1, 1, tzinfo=UTC),
            "path": PurePath("a", "b"),
            "enum": Choice.A,
            "dataclass": Value(1),
            "float": 1.5,
        }
    )
    assert b"dataclass" in encoded
    with pytest.raises(FingerprintError):
        canonical_bytes(float("nan"))


@pytest.mark.asyncio
async def test_metadata_and_token_strategies() -> None:
    snapshot = ResourceSnapshot(
        "r",
        metadata={"a": 1},
        version_token="version-1",
        etag="etag-1",
    )
    assert (await MetadataHash().fingerprint(snapshot)).algorithm == "sha256:metadata"
    assert (
        await VersionTokenFingerprint().fingerprint(snapshot)
    ).algorithm == "sha256:version-token"
    assert (await ETagFingerprint().fingerprint(snapshot)).algorithm == "sha256:etag"
    with pytest.raises(FingerprintError):
        await VersionTokenFingerprint().fingerprint(ResourceSnapshot("missing"))
    with pytest.raises(FingerprintError):
        await ETagFingerprint().fingerprint(ResourceSnapshot("missing"))
