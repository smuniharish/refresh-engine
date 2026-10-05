"""Deterministic fingerprint strategies and canonical value hashing.

[`canonical_digest`][refresh_engine.canonical_digest] encodes a value into a
canonical, type-tagged, length-prefixed byte string and returns its SHA-256 digest.
Two values have the same digest exactly when they have the same canonical form:
the same types and the same contents. Mapping insertion order and set iteration
order are ignored, `list` and `tuple` are interchangeable, and so are `bytes`,
`bytearray`, and `memoryview`. Digests are stable across processes, platforms, and
Python versions. Because types are part of the encoding, `1`, `1.0`, `True`, and
`"1"` all have different digests even though some of them compare equal.

Supported values are `None`, `bool`, `int`, finite `float`, `str`, `bytes`,
`bytearray`, `memoryview`, finite `Decimal`, `UUID`, `datetime`, `date`, `time`,
`timedelta`, pure paths, enumerations, dataclass instances, and mappings, sets, and
sequences of supported values. Enumerations and dataclasses are encoded with their
qualified type name.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import struct
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from pathlib import PurePath
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from refresh_engine.contracts import FingerprintStrategyABC
from refresh_engine.errors import FingerprintError
from refresh_engine.models import ResourceFingerprint, ResourceSnapshot

__all__ = [
    "CompositeHash",
    "ContentHash",
    "ETagFingerprint",
    "MetadataHash",
    "VersionTokenFingerprint",
    "canonical_digest",
]

type _Encoder = Callable[[Any, bytearray], None]

_LENGTH: Final = struct.Struct(">Q").pack
_FLOAT: Final = struct.Struct(">d").pack


def canonical_digest(value: object) -> str:
    """Return the SHA-256 hex digest of the canonical encoding of `value`.

    Use it to build custom fingerprint strategies.

    Args:
        value: A value composed of the supported types listed in this module.

    Returns:
        A 64-character lowercase hexadecimal digest.

    Raises:
        FingerprintError: If `value` contains an unsupported type, a non-finite
            number, or a reference cycle.

    Example:
        ```python
        class TitleHash(FingerprintStrategyABC):
            async def fingerprint(self, snapshot, /):
                title = snapshot.content["title"]
                return ResourceFingerprint("sha256:title", canonical_digest(title))
        ```
    """
    buffer = bytearray()
    try:
        _encode(value, buffer)
    except RecursionError:
        raise FingerprintError(
            "value is nested too deeply or contains a reference cycle"
        ) from None
    return hashlib.sha256(buffer).hexdigest()


def _encode(value: Any, out: bytearray) -> None:
    encoder = _EXACT.get(type(value))
    if encoder is None:
        encoder = _encoder_for(type(value))
    encoder(value, out)


def _text(tag: bytes, text: str, out: bytearray) -> None:
    data = text.encode("utf-8", "surrogatepass")
    out += tag
    out += _LENGTH(len(data))
    out += data


def _none(_: None, out: bytearray) -> None:
    out += b"N"


def _bool(value: bool, out: bytearray) -> None:
    out += b"T" if value else b"F"


def _int(value: int, out: bytearray) -> None:
    data = value.to_bytes((value.bit_length() + 8) // 8, "big", signed=True)
    out += b"I"
    out += _LENGTH(len(data))
    out += data


def _float(value: float, out: bytearray) -> None:
    if not math.isfinite(value):
        raise FingerprintError("non-finite floats cannot be fingerprinted")
    out += b"D"
    out += _FLOAT(value)


def _str(value: str, out: bytearray) -> None:
    _text(b"S", value, out)


def _bytes(value: bytes | bytearray | memoryview, out: bytearray) -> None:
    data = value.tobytes() if isinstance(value, memoryview) else value
    out += b"B"
    out += _LENGTH(len(data))
    out += data


def _sequence(value: Sequence[Any], out: bytearray) -> None:
    out += b"L"
    out += _LENGTH(len(value))
    for item in value:
        _encode(item, out)


def _mapping(value: Mapping[Any, Any], out: bytearray) -> None:
    entries = []
    for key, item in value.items():
        encoded_key = bytearray()
        _encode(key, encoded_key)
        encoded_item = bytearray()
        _encode(item, encoded_item)
        entries.append((encoded_key, encoded_item))
    entries.sort()
    out += b"M"
    out += _LENGTH(len(entries))
    for encoded_key, encoded_item in entries:
        out += encoded_key
        out += encoded_item


def _set(value: AbstractSet[Any], out: bytearray) -> None:
    items = []
    for item in value:
        encoded = bytearray()
        _encode(item, encoded)
        items.append(encoded)
    items.sort()
    out += b"E"
    out += _LENGTH(len(items))
    for encoded in items:
        out += encoded


def _datetime(value: datetime, out: bytearray) -> None:
    _text(b"Z", value.isoformat(), out)


def _date(value: date, out: bytearray) -> None:
    _text(b"Y", value.isoformat(), out)


def _time(value: time, out: bytearray) -> None:
    _text(b"H", value.isoformat(), out)


def _timedelta(value: timedelta, out: bytearray) -> None:
    out += b"W"
    _int(value.days, out)
    _int(value.seconds, out)
    _int(value.microseconds, out)


def _decimal(value: Decimal, out: bytearray) -> None:
    if not value.is_finite():
        raise FingerprintError("non-finite decimals cannot be fingerprinted")
    _text(b"C", str(value), out)


def _uuid(value: UUID, out: bytearray) -> None:
    out += b"U"
    out += value.bytes


def _path(value: PurePath, out: bytearray) -> None:
    _text(b"P", value.as_posix(), out)


def _type_name(kind: type) -> str:
    return f"{kind.__module__}.{kind.__qualname__}"


def _enum(value: Enum, out: bytearray) -> None:
    _text(b"V", _type_name(type(value)), out)
    _encode(value.value, out)


def _dataclass(value: Any, out: bytearray) -> None:
    fields = dataclasses.fields(value)
    _text(b"O", _type_name(type(value)), out)
    out += _LENGTH(len(fields))
    for item in fields:
        _text(b"S", item.name, out)
        _encode(getattr(value, item.name), out)


_EXACT: Final[dict[type, _Encoder]] = {
    type(None): _none,
    bool: _bool,
    int: _int,
    float: _float,
    str: _str,
    bytes: _bytes,
    bytearray: _bytes,
    memoryview: _bytes,
    list: _sequence,
    tuple: _sequence,
    dict: _mapping,
    MappingProxyType: _mapping,
    set: _set,
    frozenset: _set,
    datetime: _datetime,
    date: _date,
    time: _time,
    timedelta: _timedelta,
    Decimal: _decimal,
    UUID: _uuid,
}

_BY_BASE: Final[tuple[tuple[type, _Encoder], ...]] = (
    (Enum, _enum),
    (bool, _bool),
    (int, _int),
    (float, _float),
    (str, _str),
    (bytes, _bytes),
    (bytearray, _bytes),
    (datetime, _datetime),
    (date, _date),
    (time, _time),
    (timedelta, _timedelta),
    (Decimal, _decimal),
    (UUID, _uuid),
    (PurePath, _path),
)


@lru_cache(maxsize=512)
def _encoder_for(kind: type) -> _Encoder:
    for base, encoder in _BY_BASE:
        if issubclass(kind, base):
            return encoder
    if dataclasses.is_dataclass(kind):
        return _dataclass
    if issubclass(kind, Mapping):
        return _mapping
    if issubclass(kind, AbstractSet):
        return _set
    if issubclass(kind, Sequence):
        return _sequence
    raise FingerprintError(f"unsupported fingerprint value type: {_type_name(kind)}")


def _fingerprint(algorithm: str, value: object) -> ResourceFingerprint:
    return ResourceFingerprint(algorithm, canonical_digest(value))


class CompositeHash(FingerprintStrategyABC):
    """Fingerprint content, metadata, version, version token, and ETag together.

    This is the default strategy. Dependencies are not part of the fingerprint;
    the engine tracks dependency changes separately.
    """

    __slots__ = ()

    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        """Return a `sha256:composite` fingerprint of the snapshot."""
        return _fingerprint(
            "sha256:composite",
            (
                snapshot.content,
                snapshot.metadata,
                snapshot.version,
                snapshot.version_token,
                snapshot.etag,
            ),
        )


class ContentHash(FingerprintStrategyABC):
    """Fingerprint only the snapshot content."""

    __slots__ = ()

    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        """Return a `sha256:content` fingerprint of the snapshot content."""
        return _fingerprint("sha256:content", snapshot.content)


class MetadataHash(FingerprintStrategyABC):
    """Fingerprint only the snapshot metadata."""

    __slots__ = ()

    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        """Return a `sha256:metadata` fingerprint of the snapshot metadata."""
        return _fingerprint("sha256:metadata", snapshot.metadata)


class VersionTokenFingerprint(FingerprintStrategyABC):
    """Use the source-provided version token as the fingerprint."""

    __slots__ = ()

    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        """Return a `version-token` fingerprint.

        Raises:
            FingerprintError: If the snapshot has no version token.
        """
        if not snapshot.version_token:
            raise FingerprintError("the snapshot has no version_token")
        return ResourceFingerprint("version-token", snapshot.version_token)


class ETagFingerprint(FingerprintStrategyABC):
    """Use the source-provided ETag as the fingerprint."""

    __slots__ = ()

    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        """Return an `etag` fingerprint.

        Raises:
            FingerprintError: If the snapshot has no ETag.
        """
        if not snapshot.etag:
            raise FingerprintError("the snapshot has no etag")
        return ResourceFingerprint("etag", snapshot.etag)
