"""Validation and immutability helpers shared by the public models."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final

from refresh_engine.errors import ConfigurationError

EMPTY_MAPPING: Final[Mapping[str, Any]] = MappingProxyType({})


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return an immutable shallow copy of `value`."""
    if not value:
        return EMPTY_MAPPING
    return MappingProxyType(dict(value))


def require_id(value: object, name: str) -> str:
    """Return `value` if it is a non-empty string."""
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{name} must be a non-empty string")
    return value


def optional_text(value: object, name: str) -> str | None:
    """Return `value` if it is `None` or a string."""
    if value is not None and not isinstance(value, str):
        raise ConfigurationError(f"{name} must be a string or None")
    return value


def id_set(value: str | Iterable[str], name: str) -> frozenset[str]:
    """Return `value` as a set of IDs; a single string is treated as one ID."""
    items: tuple[str, ...] | frozenset[str]
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, frozenset):
        items = value
    else:
        items = tuple(value)
    for item in items:
        if not isinstance(item, str) or not item:
            raise ConfigurationError(f"{name} must contain only non-empty strings")
    return items if isinstance(items, frozenset) else frozenset(items)


def text_tuple(value: str | Iterable[str], name: str) -> tuple[str, ...]:
    """Return `value` as a tuple of strings; a single string becomes one item."""
    items = (value,) if isinstance(value, str) else tuple(value)
    for item in items:
        if not isinstance(item, str):
            raise ConfigurationError(f"{name} must contain only strings")
    return items


def number(
    value: object,
    name: str,
    *,
    minimum: float,
    exclusive: bool = False,
) -> float:
    """Return `value` if it is a finite real number within the allowed range."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
    ):
        raise ConfigurationError(f"{name} must be a finite number")
    if value < minimum or (exclusive and value == minimum):
        bound = "greater than" if exclusive else "at least"
        raise ConfigurationError(f"{name} must be {bound} {minimum:g}")
    return value


def optional_timeout(value: object, name: str) -> float | None:
    """Return `value` if it is `None` or a positive finite number of seconds."""
    if value is None:
        return None
    return number(value, name, minimum=0, exclusive=True)


def positive_int(value: object, name: str) -> int:
    """Return `value` if it is an integer of at least one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigurationError(f"{name} must be an integer of at least 1")
    return value
