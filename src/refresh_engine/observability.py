"""Structured logging and metrics derived from lifecycle events.

Subscribe [`StructlogEventHandler`][refresh_engine.StructlogEventHandler] and
[`MetricsEventHandler`][refresh_engine.MetricsEventHandler] to an
[`EventPublisher`][refresh_engine.EventPublisher] to log and measure refreshes
without touching application code.
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, Final

import structlog
from structlog.typing import FilteringBoundLogger

from refresh_engine._internal.values import require_id
from refresh_engine.contracts import MetricsSink, MetricsSinkABC
from refresh_engine.errors import ConfigurationError
from refresh_engine.events import EventKind, RefreshEvent

__all__ = [
    "InMemoryMetrics",
    "MetricsEventHandler",
    "StructlogEventHandler",
    "default_redactor",
]

_REDACTED: Final = "[REDACTED]"
_SENSITIVE_PARTS: Final = re.compile(
    r"pass(?:word|wd|phrase)|secret|token|api_?key|authori[sz]ation|cookie"
    r"|credential|private_?key|session_?(?:id|key)|bearer|connection_?string"
)
_SENSITIVE_WORDS: Final = re.compile(r"(?:^|_)(?:auth|pwd|dsn)(?:$|_)")
_WORD_BOUNDARIES: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEPARATORS: Final = re.compile(r"[\s.\-]+")

_LOG_LEVELS: Final = frozenset({"debug", "info", "warning", "error", "critical"})
_DEFAULT_LEVELS: Final[Mapping[EventKind, str]] = MappingProxyType(
    {
        EventKind.RESOURCE_REFRESH_STARTED: "debug",
        EventKind.RESOURCE_REFRESH_COMPLETED: "debug",
        EventKind.RESOURCE_SNAPSHOT_FAILED: "warning",
        EventKind.RESOURCE_REFRESH_FAILED: "warning",
        EventKind.RESOURCE_REFRESH_SKIPPED: "warning",
        EventKind.REFRESH_FAILED: "error",
        EventKind.REFRESH_CANCELLED: "warning",
        EventKind.SCHEDULER_RUN_FAILED: "error",
    }
)
_RESERVED_FIELDS: Final = frozenset(
    {
        "correlation_id",
        "event",
        "exc_info",
        "occurred_at",
        "refresh_id",
        "resource_id",
    }
)
_TERMINAL: Final = frozenset(
    {
        EventKind.REFRESH_COMPLETED,
        EventKind.REFRESH_FAILED,
        EventKind.REFRESH_CANCELLED,
        EventKind.REFRESH_SKIPPED,
    }
)
_CHANGES: Final = ("added", "modified", "dependency_changed", "unchanged", "deleted")


def _is_sensitive(key: object) -> bool:
    words = _WORD_BOUNDARIES.sub("_", str(key))
    normalized = _SEPARATORS.sub("_", words.lower())
    return bool(
        _SENSITIVE_PARTS.search(normalized) or _SENSITIVE_WORDS.search(normalized)
    )


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return default_redactor(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def default_redactor(values: Mapping[str, Any]) -> dict[str, Any]:
    """Replace the values of credential-like keys with `"[REDACTED]"`.

    Keys are matched case-insensitively against common credential names such as
    `password`, `secret`, `token`, `api_key`, `authorization`, `cookie`,
    `credential`, `private_key`, `session_id`, `auth`, and `dsn`. Keys written in
    camelCase, kebab-case, or dotted form are split into words first, so
    `accessToken`, `x-api-key`, and `dbPwd` are redacted too. Nested mappings,
    lists, and tuples are redacted recursively.

    Args:
        values: Fields to redact.

    Returns:
        A new dictionary with sensitive values replaced.
    """
    return {
        key: _REDACTED if _is_sensitive(key) else _redact(value)
        for key, value in values.items()
    }


class StructlogEventHandler:
    """Log lifecycle events as structlog events.

    Each event is logged with its kind as the event name (for example
    `refresh_completed`), the fields `refresh_id`, `resource_id`, `correlation_id`,
    and `occurred_at` when present, and the event attributes. Attributes that
    collide with those fields are prefixed with `attr_`. Failure events carry the
    exception as `exc_info`.

    The handler does not configure structlog; the application owns processors and
    rendering.

    Args:
        logger: Logger to use; defaults to `structlog.get_logger("refresh_engine")`.
        redactor: Function applied to every field mapping before logging.
        levels: Log level overrides by event kind. By default, per-resource start
            and success events log at `debug`; resource failures, skipped
            operations, and cancellations at `warning`; refresh and scheduler
            failures at `error`; and everything else at `info`.

    Raises:
        ConfigurationError: If a level is not a known log level.
    """

    def __init__(
        self,
        logger: FilteringBoundLogger | None = None,
        *,
        redactor: Callable[[Mapping[str, Any]], Mapping[str, Any]] = default_redactor,
        levels: Mapping[EventKind, str] | None = None,
    ) -> None:
        """Create a handler."""
        self._logger = (
            logger if logger is not None else structlog.get_logger("refresh_engine")
        )
        self._redactor = redactor
        merged = dict(_DEFAULT_LEVELS)
        for kind, level in (levels or {}).items():
            if level not in _LOG_LEVELS:
                raise ConfigurationError(
                    f"unknown log level {level!r}; use one of: "
                    + ", ".join(sorted(_LOG_LEVELS))
                )
            merged[EventKind(kind)] = level
        self._levels = merged

    def __call__(self, event: RefreshEvent) -> None:
        """Log one event."""
        fields: dict[str, Any] = {}
        for key, value in event.attributes.items():
            fields[f"attr_{key}" if key in _RESERVED_FIELDS else key] = value
        if event.refresh_id is not None:
            fields["refresh_id"] = event.refresh_id
        if event.resource_id is not None:
            fields["resource_id"] = event.resource_id
        if event.correlation_id is not None:
            fields["correlation_id"] = event.correlation_id
        fields["occurred_at"] = event.timestamp.isoformat()
        safe = dict(self._redactor(fields))
        if event.error is not None:
            safe["exc_info"] = event.error
        log = getattr(self._logger, self._levels.get(event.kind, "info"))
        log(event.kind.value, **safe)


class MetricsEventHandler:
    """Translate lifecycle events into counters and observations.

    | Metric | Kind | Attributes |
    | --- | --- | --- |
    | `{prefix}.refreshes` | counter | `status`, `mode`, `trigger` |
    | `{prefix}.refresh.duration` | observation (seconds) | `status`, `mode` |
    | `{prefix}.resources.detected` | counter | `change` |
    | `{prefix}.operations` | counter | `action`, `outcome` |
    | `{prefix}.operation.duration` | observation (seconds) | `action`, `outcome` |
    | `{prefix}.snapshot.failures` | counter | `phase` |
    | `{prefix}.scheduler.failures` | counter | none |

    `outcome` is `success`, `failure`, or `skipped`; `change` is one of `added`,
    `modified`, `dependency_changed`, `unchanged`, or `deleted`.

    Args:
        sink: Destination for the metrics.
        prefix: Prefix of every metric name.

    Raises:
        ConfigurationError: If `prefix` is empty.
    """

    def __init__(self, sink: MetricsSink, *, prefix: str = "refresh_engine") -> None:
        """Create a handler that reports to `sink`."""
        self._sink = sink
        self._prefix = require_id(prefix, "prefix")

    def __call__(self, event: RefreshEvent) -> None:
        """Record the metrics derived from one event."""
        kind = event.kind
        values = event.attributes
        prefix = self._prefix
        if kind in _TERMINAL:
            status = str(values.get("status"))
            mode = str(values.get("mode"))
            self._sink.increment(
                f"{prefix}.refreshes",
                1,
                {"status": status, "mode": mode, "trigger": str(values.get("trigger"))},
            )
            self._observe(
                f"{prefix}.refresh.duration",
                values.get("duration"),
                {"status": status, "mode": mode},
            )
        elif kind is EventKind.DETECTION_COMPLETED:
            for change in _CHANGES:
                count = values.get(change)
                if isinstance(count, int) and count > 0:
                    self._sink.increment(
                        f"{prefix}.resources.detected", count, {"change": change}
                    )
        elif kind in (
            EventKind.RESOURCE_REFRESH_COMPLETED,
            EventKind.RESOURCE_REFRESH_FAILED,
            EventKind.RESOURCE_REFRESH_SKIPPED,
        ):
            outcome = {
                EventKind.RESOURCE_REFRESH_COMPLETED: "success",
                EventKind.RESOURCE_REFRESH_FAILED: "failure",
                EventKind.RESOURCE_REFRESH_SKIPPED: "skipped",
            }[kind]
            attributes = {"action": str(values.get("action")), "outcome": outcome}
            self._sink.increment(f"{prefix}.operations", 1, attributes)
            self._observe(
                f"{prefix}.operation.duration", values.get("duration"), attributes
            )
        elif kind is EventKind.RESOURCE_SNAPSHOT_FAILED:
            self._sink.increment(
                f"{prefix}.snapshot.failures", 1, {"phase": str(values.get("phase"))}
            )
        elif kind is EventKind.SCHEDULER_RUN_FAILED:
            self._sink.increment(f"{prefix}.scheduler.failures", 1, None)

    def _observe(self, name: str, value: object, attributes: Mapping[str, str]) -> None:
        if isinstance(value, int | float) and not isinstance(value, bool):
            self._sink.observe(name, float(value), attributes)


class InMemoryMetrics(MetricsSinkABC):
    """Thread-safe in-memory metric sink for tests and small deployments.

    Metrics are keyed by name and attributes, formatted as
    `name{key=value,...}` with attributes sorted by key.
    """

    def __init__(self) -> None:
        """Create an empty sink."""
        self._counters: defaultdict[str, int] = defaultdict(int)
        self._observations: defaultdict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def increment(
        self,
        name: str,
        value: int = 1,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        """Add `value` to the counter `name`."""
        key = self._key(name, attributes)
        with self._lock:
            self._counters[key] += value

    def observe(
        self,
        name: str,
        value: float,
        attributes: Mapping[str, str] | None = None,
        /,
    ) -> None:
        """Record one observation of `value` for the metric `name`."""
        key = self._key(name, attributes)
        with self._lock:
            self._observations[key].append(value)

    def snapshot(
        self,
    ) -> tuple[Mapping[str, int], Mapping[str, tuple[float, ...]]]:
        """Return immutable copies of the counters and observations."""
        with self._lock:
            counters = MappingProxyType(dict(self._counters))
            observations = MappingProxyType(
                {key: tuple(values) for key, values in self._observations.items()}
            )
        return counters, observations

    @staticmethod
    def _key(name: str, attributes: Mapping[str, str] | None) -> str:
        if not attributes:
            return name
        labels = ",".join(f"{key}={attributes[key]}" for key in sorted(attributes))
        return f"{name}{{{labels}}}"
