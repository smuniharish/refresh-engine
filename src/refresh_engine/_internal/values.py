"""Validation and immutability helpers shared by the public models."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final, NoReturn

from refresh_engine.errors import ConfigurationError

EMPTY_MAPPING: Final[Mapping[str, Any]] = MappingProxyType({})


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def freeze_mapping(
    value: Mapping[str, Any] | None, name: str = "metadata"
) -> Mapping[str, Any]:
    """Return an immutable, recursively frozen copy of `value`.

    Built-in containers are copied into immutable equivalents that still compare
    equal to the originals and serialize the same way: `dict` and mapping
    proxies become read-only `dict` subclasses, `list` becomes a read-only `list`
    subclass, `set` becomes `frozenset`, and tuples are rebuilt from frozen items.
    Any other object, including container subclasses, is kept by reference.

    Raises:
        ConfigurationError: If `value` contains a reference cycle or is nested
            too deeply.
    """
    if not value:
        return EMPTY_MAPPING
    active: set[int] = set()
    done: dict[int, Any] = {}
    try:
        items = {key: _freeze(item, active, done) for key, item in dict(value).items()}
    except _CycleError:
        raise ConfigurationError(f"{name} must not contain reference cycles") from None
    except RecursionError:
        raise ConfigurationError(f"{name} is nested too deeply") from None
    return MappingProxyType(items)


class _CycleError(Exception):
    pass


def _immutable(self: object, *args: object, **kwargs: object) -> NoReturn:
    raise TypeError(f"{type(self).__name__} is immutable")


class FrozenDict(dict[Any, Any]):
    """A `dict` that rejects mutation; produced by `freeze_mapping`."""

    __slots__ = ()
    __setitem__ = __delitem__ = __ior__ = _immutable
    clear = pop = popitem = setdefault = update = _immutable

    def __reduce__(self) -> tuple[type[FrozenDict], tuple[dict[Any, Any]]]:
        """Pickle and copy through the constructor."""
        return (FrozenDict, (dict(self),))


class FrozenList(list[Any]):
    """A `list` that rejects mutation; produced by `freeze_mapping`."""

    __slots__ = ()
    __setitem__ = __delitem__ = __iadd__ = __imul__ = _immutable
    append = extend = insert = pop = remove = clear = sort = reverse = _immutable

    def __reduce__(self) -> tuple[type[FrozenList], tuple[list[Any]]]:
        """Pickle and copy through the constructor."""
        return (FrozenList, (list(self),))


_PASSTHROUGH: Final = frozenset(
    {str, int, float, bool, type(None), bytes, frozenset, FrozenDict, FrozenList}
)


def _freeze(value: Any, active: set[int], done: dict[int, Any]) -> Any:
    kind = type(value)
    if kind in _PASSTHROUGH:
        return value
    if kind not in (dict, MappingProxyType, list, tuple, set):
        return value
    key = id(value)
    frozen = done.get(key)
    if frozen is not None:
        return frozen
    if key in active:
        raise _CycleError
    active.add(key)
    if kind is dict or kind is MappingProxyType:
        frozen = FrozenDict(
            {item: _freeze(entry, active, done) for item, entry in value.items()}
        )
    elif kind is list:
        frozen = FrozenList([_freeze(item, active, done) for item in value])
    elif kind is tuple:
        frozen = tuple(_freeze(item, active, done) for item in value)
    else:
        frozen = frozenset(value)
    active.discard(key)
    done[key] = frozen
    return frozen


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


def strict_bool(value: object, name: str) -> bool:
    """Return `value` if it is a `bool`; truthy stand-ins such as `"false"` fail."""
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a bool, not {type(value).__name__}")
    return value


def positive_int(value: object, name: str) -> int:
    """Return `value` if it is an integer of at least one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigurationError(f"{name} must be an integer of at least 1")
    return value
