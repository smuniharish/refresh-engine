"""Deterministic fingerprint strategies."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections.abc import Mapping, Sequence, Set
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import PurePath
from typing import Any

from refresh_engine.api.abc import FingerprintStrategyABC
from refresh_engine.core.models import ResourceFingerprint, ResourceSnapshot
from refresh_engine.errors import FingerprintError


def _canonical(value: Any) -> Any:
    if value is None:
        return ["null", None]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FingerprintError("non-finite floats cannot be fingerprinted")
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, (datetime, date)):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, PurePath):
        return ["path", value.as_posix()]
    if isinstance(value, Enum):
        return [
            "enum",
            f"{type(value).__module__}.{type(value).__qualname__}",
            value.value,
        ]
    if is_dataclass(value) and not isinstance(value, type):
        return ["dataclass", _canonical(asdict(value))]
    if isinstance(value, Mapping):
        pairs = [(_canonical(key), _canonical(item)) for key, item in value.items()]
        pairs.sort(key=lambda pair: _encode(pair[0]))
        return ["mapping", pairs]
    if isinstance(value, Set):
        items = [_canonical(item) for item in value]
        items.sort(key=_encode)
        return ["set", items]
    if isinstance(value, Sequence):
        return ["sequence", [_canonical(item) for item in value]]
    raise FingerprintError(f"unsupported fingerprint value: {type(value).__qualname__}")


def _encode(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def canonical_bytes(value: Any) -> bytes:
    """Encode a supported value deterministically and with type information."""
    return _encode(_canonical(value))


def _sha256(namespace: str, value: Any) -> ResourceFingerprint:
    digest = hashlib.sha256(canonical_bytes(value)).hexdigest()
    return ResourceFingerprint(f"sha256:{namespace}", digest)


class ContentHash(FingerprintStrategyABC):
    async def fingerprint(self, snapshot: ResourceSnapshot) -> ResourceFingerprint:
        return _sha256("content", snapshot.content)


class MetadataHash(FingerprintStrategyABC):
    async def fingerprint(self, snapshot: ResourceSnapshot) -> ResourceFingerprint:
        return _sha256("metadata", snapshot.metadata)


class CompositeHash(FingerprintStrategyABC):
    def __init__(self, *, include_dependencies: bool = True) -> None:
        self._include_dependencies = include_dependencies

    async def fingerprint(self, snapshot: ResourceSnapshot) -> ResourceFingerprint:
        value = {
            "content": snapshot.content,
            "metadata": snapshot.metadata,
            "version": snapshot.version,
            "version_token": snapshot.version_token,
            "etag": snapshot.etag,
        }
        if self._include_dependencies:
            value["dependencies"] = sorted(snapshot.dependencies)
        return _sha256("composite", value)


class VersionTokenFingerprint(FingerprintStrategyABC):
    async def fingerprint(self, snapshot: ResourceSnapshot) -> ResourceFingerprint:
        if snapshot.version_token is None:
            raise FingerprintError("snapshot has no version_token")
        return _sha256("version-token", snapshot.version_token)


class ETagFingerprint(FingerprintStrategyABC):
    async def fingerprint(self, snapshot: ResourceSnapshot) -> ResourceFingerprint:
        if snapshot.etag is None:
            raise FingerprintError("snapshot has no etag")
        return _sha256("etag", snapshot.etag)
