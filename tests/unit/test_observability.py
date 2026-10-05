"""Tests for structured logging, metrics, and redaction in `observability`."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

import pytest
import structlog
from structlog.testing import LogCapture, capture_logs

from refresh_engine import (
    ConfigurationError,
    EventKind,
    InMemoryMetrics,
    MetricsEventHandler,
    MetricsSink,
    MetricsSinkABC,
    RefreshEvent,
    StructlogEventHandler,
    default_redactor,
)

REDACTED = "[REDACTED]"
TERMINAL_STATUSES = {
    EventKind.REFRESH_COMPLETED: "success",
    EventKind.REFRESH_FAILED: "failed",
    EventKind.REFRESH_CANCELLED: "cancelled",
    EventKind.REFRESH_SKIPPED: "skipped",
}
OPERATION_OUTCOMES = {
    EventKind.RESOURCE_REFRESH_COMPLETED: "success",
    EventKind.RESOURCE_REFRESH_FAILED: "failure",
    EventKind.RESOURCE_REFRESH_SKIPPED: "skipped",
}
METRIC_KINDS = {
    *TERMINAL_STATUSES,
    *OPERATION_OUTCOMES,
    EventKind.DETECTION_COMPLETED,
    EventKind.RESOURCE_SNAPSHOT_FAILED,
    EventKind.SCHEDULER_RUN_FAILED,
}
DEFAULT_LEVELS = {
    EventKind.RESOURCE_REFRESH_STARTED: "debug",
    EventKind.RESOURCE_REFRESH_COMPLETED: "debug",
    EventKind.RESOURCE_SNAPSHOT_FAILED: "warning",
    EventKind.RESOURCE_REFRESH_FAILED: "warning",
    EventKind.RESOURCE_REFRESH_SKIPPED: "warning",
    EventKind.REFRESH_CANCELLED: "warning",
    EventKind.REFRESH_FAILED: "error",
    EventKind.SCHEDULER_RUN_FAILED: "error",
}
TIMESTAMP = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)


class Field(StrEnum):
    SENSITIVE = "token"


def kind_id(kind: object) -> str:
    return kind.value if isinstance(kind, EventKind) else str(kind)


def log_event(event: RefreshEvent, **options: Any) -> list[MutableMapping[str, Any]]:
    with capture_logs() as logs:
        StructlogEventHandler(**options)(event)
    return logs


def log_one(event: RefreshEvent, **options: Any) -> MutableMapping[str, Any]:
    (entry,) = log_event(event, **options)
    return entry


def record_metrics(
    *events: RefreshEvent, prefix: str = "refresh_engine"
) -> tuple[Mapping[str, int], Mapping[str, tuple[float, ...]]]:
    metrics = InMemoryMetrics()
    handler = MetricsEventHandler(metrics, prefix=prefix)
    for event in events:
        handler(event)
    return metrics.snapshot()


# default_redactor


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PASSWORD",
        "Password",
        "passwd",
        "passphrase",
        "secret",
        "client_secret",
        "token",
        "access_token",
        "accessToken",
        "api_key",
        "apikey",
        "apiKey",
        "API-KEY",
        "api.key",
        "api key",
        "authorization",
        "Authorization",
        "authorisation",
        "cookie",
        "Set-Cookie",
        "credential",
        "credentials",
        "private_key",
        "privateKey",
        "private-key",
        "session_id",
        "sessionId",
        "session.key",
        "bearer",
        "connection_string",
        "connectionString",
        "connection-string",
        "auth",
        "AUTH",
        "x-auth",
        "auth_header",
        "pwd",
        "db.pwd",
        "dsn",
        "sentry-dsn",
        "DATABASE_DSN",
        "dbPwd",
        "sentryDsn",
        "xAuth",
        "XAuthToken",
        "XAPIKey",
    ],
)
def test_default_redactor_redacts_credential_like_keys(key: str) -> None:
    assert default_redactor({key: "value"}) == {key: REDACTED}


@pytest.mark.parametrize(
    "key",
    [
        "author",
        "authority",
        "co-author",
        "authorName",
        "AuthorityLevel",
        "name",
        "mode",
        "duration",
        "error_type",
        "refresh_id",
        "resource_id",
        "correlation_id",
        "occurred_at",
    ],
)
def test_default_redactor_keeps_ordinary_keys(key: str) -> None:
    assert default_redactor({key: "value"}) == {key: "value"}


def test_default_redactor_redacts_nested_mappings() -> None:
    values = {"outer": {"password": "p", "user": "u", "inner": {"dsn": "d"}}}

    assert default_redactor(values) == {
        "outer": {"password": REDACTED, "user": "u", "inner": {"dsn": REDACTED}}
    }


def test_default_redactor_redacts_inside_lists_and_tuples() -> None:
    values = {"items": [{"token": "t"}, 1, ({"secret": "s"}, "plain")]}

    redacted = default_redactor(values)

    assert redacted == {
        "items": [{"token": REDACTED}, 1, ({"secret": REDACTED}, "plain")]
    }
    assert type(redacted["items"]) is list
    assert type(redacted["items"][2]) is tuple


def test_default_redactor_replaces_the_whole_value_of_a_sensitive_key() -> None:
    values = {"credentials": {"user": "u", "region": "eu"}}

    assert default_redactor(values) == {"credentials": REDACTED}


def test_default_redactor_leaves_other_values_untouched() -> None:
    payload = object()
    tags = {"a", "b"}

    redacted = default_redactor({"payload": payload, "tags": tags, "note": "password"})

    assert redacted["payload"] is payload
    assert redacted["tags"] is tags
    assert redacted["note"] == "password"


def test_default_redactor_matches_non_string_keys_by_their_text() -> None:
    values: dict[Any, Any] = {1: "one", None: "none", Field.SENSITIVE: "t"}

    assert default_redactor(values) == {
        1: "one",
        None: "none",
        Field.SENSITIVE: REDACTED,
    }


def test_default_redactor_returns_a_new_dict_without_mutating_the_input() -> None:
    values = {"password": "p", "nested": {"token": "t"}}

    redacted = default_redactor(values)

    assert values == {"password": "p", "nested": {"token": "t"}}
    assert redacted is not values
    assert type(redacted) is dict


# StructlogEventHandler


def test_structlog_handler_logs_the_kind_as_the_event_name() -> None:
    entry = log_one(RefreshEvent(EventKind.REFRESH_COMPLETED))

    assert entry["event"] == "refresh_completed"


def test_structlog_handler_logs_identifiers_when_present() -> None:
    entry = log_one(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_FAILED,
            refresh_id="r-1",
            resource_id="alpha",
            correlation_id="c-1",
        )
    )

    assert entry["refresh_id"] == "r-1"
    assert entry["resource_id"] == "alpha"
    assert entry["correlation_id"] == "c-1"


def test_structlog_handler_omits_missing_identifiers() -> None:
    entry = log_one(RefreshEvent(EventKind.SCHEDULER_STARTED))

    assert "refresh_id" not in entry
    assert "resource_id" not in entry
    assert "correlation_id" not in entry


def test_structlog_handler_logs_the_timestamp_in_iso_format() -> None:
    entry = log_one(RefreshEvent(EventKind.REFRESH_STARTED, timestamp=TIMESTAMP))

    assert entry["occurred_at"] == "2024-01-02T03:04:05+00:00"


def test_structlog_handler_merges_attributes() -> None:
    entry = log_one(
        RefreshEvent(
            EventKind.DISCOVERY_COMPLETED,
            attributes={"discovered": 3, "complete": True},
        )
    )

    assert entry["discovered"] == 3
    assert entry["complete"] is True


@pytest.mark.parametrize(
    "key",
    [
        "event",
        "refresh_id",
        "resource_id",
        "correlation_id",
        "occurred_at",
        "exc_info",
    ],
)
def test_structlog_handler_prefixes_reserved_attribute_keys(key: str) -> None:
    entry = log_one(
        RefreshEvent(EventKind.REFRESH_STARTED, attributes={key: "attribute value"})
    )

    assert entry[f"attr_{key}"] == "attribute value"
    assert entry.get(key) != "attribute value"
    assert entry["event"] == "refresh_started"


def test_structlog_handler_redacts_fields_by_default() -> None:
    entry = log_one(
        RefreshEvent(
            EventKind.REFRESH_STARTED,
            attributes={
                "password": "hunter2",
                "nested": {"token": "t"},
                "mode": "full",
            },
        )
    )

    assert entry["password"] == REDACTED
    assert entry["nested"] == {"token": REDACTED}
    assert entry["mode"] == "full"


def test_structlog_handler_applies_a_custom_redactor_to_every_field() -> None:
    received: list[dict[str, Any]] = []
    error = ValueError("boom")

    def redactor(values: Mapping[str, Any]) -> Mapping[str, Any]:
        received.append(dict(values))
        return {
            key: "hidden" if key == "visible" else value
            for key, value in values.items()
        }

    entry = log_one(
        RefreshEvent(
            EventKind.REFRESH_FAILED,
            refresh_id="r-1",
            timestamp=TIMESTAMP,
            attributes={"visible": "secret", "password": "kept"},
            error=error,
        ),
        redactor=redactor,
    )

    assert received == [
        {
            "visible": "secret",
            "password": "kept",
            "refresh_id": "r-1",
            "occurred_at": "2024-01-02T03:04:05+00:00",
        }
    ]
    logged = {key: entry[key] for key in ("visible", "password")}
    assert logged == {"visible": "hidden", "password": "kept"}
    assert entry["exc_info"] is error


def test_structlog_handler_attaches_the_error_as_exc_info() -> None:
    error = RuntimeError("operation failed")

    entry = log_one(RefreshEvent(EventKind.RESOURCE_REFRESH_FAILED, error=error))

    assert entry["exc_info"] is error


def test_structlog_handler_omits_exc_info_without_an_error() -> None:
    entry = log_one(RefreshEvent(EventKind.REFRESH_FAILED))

    assert "exc_info" not in entry


@pytest.mark.parametrize("kind", list(EventKind), ids=kind_id)
def test_structlog_handler_default_levels(kind: EventKind) -> None:
    entry = log_one(RefreshEvent(kind))

    assert entry["log_level"] == DEFAULT_LEVELS.get(kind, "info")


def test_structlog_handler_level_overrides_replace_only_the_given_kinds() -> None:
    levels = {
        EventKind.REFRESH_COMPLETED: "critical",
        EventKind.RESOURCE_REFRESH_STARTED: "info",
    }

    def level(kind: EventKind) -> str:
        return log_one(RefreshEvent(kind), levels=levels)["log_level"]

    assert level(EventKind.REFRESH_COMPLETED) == "critical"
    assert level(EventKind.RESOURCE_REFRESH_STARTED) == "info"
    assert level(EventKind.REFRESH_FAILED) == "error"
    assert level(EventKind.RESOURCE_REFRESH_COMPLETED) == "debug"


@pytest.mark.parametrize("level", ["verbose", "WARNING", "warn", ""])
def test_structlog_handler_rejects_unknown_levels(level: str) -> None:
    message = (
        f"unknown log level {level!r}; "
        "use one of: critical, debug, error, info, warning"
    )

    with pytest.raises(ConfigurationError, match=f"^{re.escape(message)}$"):
        StructlogEventHandler(levels={EventKind.REFRESH_COMPLETED: level})


def test_structlog_handler_uses_a_custom_logger() -> None:
    capture = LogCapture()
    logger = structlog.wrap_logger(
        structlog.ReturnLogger(),
        processors=[capture],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    ).bind(component="sync")
    handler = StructlogEventHandler(logger)

    with capture_logs() as global_logs:
        handler(RefreshEvent(EventKind.REFRESH_COMPLETED, refresh_id="r-1"))
        handler(RefreshEvent(EventKind.RESOURCE_REFRESH_STARTED))

    assert global_logs == []
    (entry,) = capture.entries
    assert entry["event"] == "refresh_completed"
    assert entry["component"] == "sync"
    assert entry["refresh_id"] == "r-1"
    assert entry["log_level"] == "info"


# MetricsEventHandler


@pytest.mark.parametrize(
    ("kind", "status"), list(TERMINAL_STATUSES.items()), ids=kind_id
)
def test_terminal_events_count_refreshes_and_observe_their_duration(
    kind: EventKind, status: str
) -> None:
    counters, observations = record_metrics(
        RefreshEvent(
            kind,
            attributes={
                "status": status,
                "mode": "incremental",
                "trigger": "manual",
                "duration": 1.5,
            },
        )
    )

    labels = f"mode=incremental,status={status}"
    assert counters == {f"refresh_engine.refreshes{{{labels},trigger=manual}}": 1}
    assert observations == {f"refresh_engine.refresh.duration{{{labels}}}": (1.5,)}


def test_terminal_event_metrics_accumulate() -> None:
    attributes = {"status": "success", "mode": "full", "trigger": "scheduled"}

    counters, observations = record_metrics(
        RefreshEvent(
            EventKind.REFRESH_COMPLETED, attributes={**attributes, "duration": 1}
        ),
        RefreshEvent(
            EventKind.REFRESH_COMPLETED, attributes={**attributes, "duration": 2.5}
        ),
    )

    assert counters == {
        "refresh_engine.refreshes{mode=full,status=success,trigger=scheduled}": 2
    }
    (durations,) = observations.values()
    assert durations == (1.0, 2.5)
    assert all(type(duration) is float for duration in durations)


@pytest.mark.parametrize(
    "duration", ["1.5", True, None, [1.0]], ids=["str", "bool", "none", "list"]
)
def test_non_numeric_durations_are_not_observed(duration: object) -> None:
    counters, observations = record_metrics(
        RefreshEvent(
            EventKind.REFRESH_FAILED,
            attributes={
                "status": "failed",
                "mode": "full",
                "trigger": "manual",
                "duration": duration,
            },
        ),
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_FAILED,
            attributes={"action": "refresh", "duration": duration},
        ),
    )

    assert observations == {}
    assert sum(counters.values()) == 2


def test_detection_counts_only_positive_integer_changes() -> None:
    counters, observations = record_metrics(
        RefreshEvent(
            EventKind.DETECTION_COMPLETED,
            attributes={
                "added": 2,
                "modified": 0,
                "dependency_changed": 1,
                "unchanged": 5,
                "ignored": 7,
            },
        ),
        RefreshEvent(
            EventKind.DETECTION_COMPLETED,
            attributes={"added": 1, "modified": -1, "unchanged": "3", "deleted": 2.0},
        ),
    )

    assert counters == {
        "refresh_engine.resources.detected{change=added}": 3,
        "refresh_engine.resources.detected{change=dependency_changed}": 1,
        "refresh_engine.resources.detected{change=unchanged}": 5,
    }
    assert observations == {}


@pytest.mark.parametrize(
    ("kind", "outcome"), list(OPERATION_OUTCOMES.items()), ids=kind_id
)
def test_operation_events_count_outcomes_and_observe_durations(
    kind: EventKind, outcome: str
) -> None:
    counters, observations = record_metrics(
        RefreshEvent(kind, attributes={"action": "delete", "duration": 0.25})
    )

    assert counters == {
        f"refresh_engine.operations{{action=delete,outcome={outcome}}}": 1
    }
    assert observations == {
        f"refresh_engine.operation.duration{{action=delete,outcome={outcome}}}": (0.25,)
    }


def test_skipped_operations_without_duration_are_only_counted() -> None:
    counters, observations = record_metrics(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_SKIPPED,
            attributes={"action": "refresh", "reason": "fail_fast"},
        )
    )

    assert counters == {"refresh_engine.operations{action=refresh,outcome=skipped}": 1}
    assert observations == {}


def test_snapshot_failures_are_counted_by_phase() -> None:
    counters, _ = record_metrics(
        RefreshEvent(
            EventKind.RESOURCE_SNAPSHOT_FAILED,
            attributes={"phase": "fingerprint", "error_type": "FingerprintError"},
        )
    )

    assert counters == {"refresh_engine.snapshot.failures{phase=fingerprint}": 1}


def test_scheduler_failures_are_counted_without_attributes() -> None:
    counters, _ = record_metrics(
        RefreshEvent(
            EventKind.SCHEDULER_RUN_FAILED, attributes={"error_type": "ValueError"}
        )
    )

    assert counters == {"refresh_engine.scheduler.failures": 1}


@pytest.mark.parametrize(
    "kind", [kind for kind in EventKind if kind not in METRIC_KINDS], ids=kind_id
)
def test_other_events_do_not_produce_metrics(kind: EventKind) -> None:
    counters, observations = record_metrics(
        RefreshEvent(
            kind,
            attributes={"status": "success", "duration": 1.0, "added": 1, "phase": "x"},
        )
    )

    assert counters == {}
    assert observations == {}


def test_metrics_use_the_configured_prefix() -> None:
    counters, _ = record_metrics(
        RefreshEvent(EventKind.SCHEDULER_RUN_FAILED), prefix="app.sync"
    )

    assert counters == {"app.sync.scheduler.failures": 1}


@pytest.mark.parametrize("prefix", ["", None, 1])
def test_metrics_handler_requires_a_non_empty_prefix(prefix: object) -> None:
    with pytest.raises(
        ConfigurationError, match=f"^{re.escape('prefix must be a non-empty string')}$"
    ):
        MetricsEventHandler(InMemoryMetrics(), prefix=cast(Any, prefix))


def test_metrics_handler_reports_to_protocol_only_sinks() -> None:
    calls: list[tuple[str, str, float, Mapping[str, str] | None]] = []

    class Sink:
        def increment(
            self,
            name: str,
            value: int = 1,
            attributes: Mapping[str, str] | None = None,
            /,
        ) -> None:
            calls.append(("increment", name, value, attributes))

        def observe(
            self,
            name: str,
            value: float,
            attributes: Mapping[str, str] | None = None,
            /,
        ) -> None:
            calls.append(("observe", name, value, attributes))

    handler = MetricsEventHandler(Sink())
    handler(RefreshEvent(EventKind.SCHEDULER_RUN_FAILED))
    handler(
        RefreshEvent(
            EventKind.RESOURCE_REFRESH_COMPLETED,
            attributes={"action": "refresh", "duration": 3},
        )
    )

    operation = {"action": "refresh", "outcome": "success"}
    assert calls == [
        ("increment", "refresh_engine.scheduler.failures", 1, None),
        ("increment", "refresh_engine.operations", 1, operation),
        ("observe", "refresh_engine.operation.duration", 3.0, operation),
    ]


# InMemoryMetrics


def test_in_memory_metrics_implements_both_sink_contracts() -> None:
    metrics = InMemoryMetrics()

    assert isinstance(metrics, MetricsSinkABC)
    assert isinstance(metrics, MetricsSink)


def test_in_memory_metrics_starts_empty() -> None:
    assert InMemoryMetrics().snapshot() == ({}, {})


def test_in_memory_metrics_keys_sort_attributes_by_name() -> None:
    metrics = InMemoryMetrics()

    metrics.increment("requests", 1, {"b": "2", "a": "1"})
    metrics.observe("latency", 0.5, {"route": "/", "method": "GET"})

    counters, observations = metrics.snapshot()
    assert counters == {"requests{a=1,b=2}": 1}
    assert observations == {"latency{method=GET,route=/}": (0.5,)}


def test_in_memory_metrics_uses_the_bare_name_without_attributes() -> None:
    metrics = InMemoryMetrics()

    metrics.increment("requests")
    metrics.increment("requests", 2, {})
    metrics.increment("requests", 3, None)

    counters, _ = metrics.snapshot()
    assert counters == {"requests": 6}


def test_in_memory_metrics_keeps_observations_in_order() -> None:
    metrics = InMemoryMetrics()

    metrics.observe("latency", 0.5)
    metrics.observe("latency", 1.5)
    metrics.increment("latency")

    counters, observations = metrics.snapshot()
    assert observations == {"latency": (0.5, 1.5)}
    assert counters == {"latency": 1}


def test_in_memory_metrics_snapshots_are_immutable_copies() -> None:
    metrics = InMemoryMetrics()
    metrics.increment("requests")
    metrics.observe("latency", 0.5)

    counters, observations = metrics.snapshot()
    metrics.increment("requests")
    metrics.observe("latency", 1.0)

    assert counters == {"requests": 1}
    assert observations == {"latency": (0.5,)}
    with pytest.raises(TypeError):
        cast(Any, counters)["requests"] = 10
    with pytest.raises(TypeError):
        cast(Any, observations)["latency"] = ()


def test_in_memory_metrics_is_thread_safe() -> None:
    metrics = InMemoryMetrics()
    threads_count, iterations = 8, 2_000
    barrier = threading.Barrier(threads_count)

    def work() -> None:
        barrier.wait(timeout=5)
        for _ in range(iterations):
            metrics.increment("requests", 1, {"worker": "any"})
            metrics.observe("latency", 1.0)

    threads = [threading.Thread(target=work) for _ in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    counters, observations = metrics.snapshot()
    assert counters == {"requests{worker=any}": threads_count * iterations}
    assert len(observations["latency"]) == threads_count * iterations
