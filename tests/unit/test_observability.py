"""Tests for structured logging, metrics, and redaction in `observability`."""

from __future__ import annotations

import asyncio
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
    AsyncEventPublisher,
    ConfigurationError,
    EventKind,
    InMemoryMetrics,
    MetricsEventHandler,
    MetricsSink,
    RefreshEvent,
    StructlogEventHandler,
    default_redactor,
    redact_exception,
    redact_text,
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
        asyncio.run(StructlogEventHandler(**options)(event))
    return logs


def log_one(event: RefreshEvent, **options: Any) -> MutableMapping[str, Any]:
    (entry,) = log_event(event, **options)
    return entry


def record_metrics(
    *events: RefreshEvent, prefix: str = "refresh_engine"
) -> tuple[Mapping[str, int], Mapping[str, tuple[float, ...]]]:
    metrics = InMemoryMetrics()
    handler = MetricsEventHandler(metrics, prefix=prefix)

    async def record() -> None:
        for event in events:
            await handler(event)

    asyncio.run(record())
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
        "exception",
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
    assert entry["exception"] == "ValueError: boom"
    assert "exc_info" not in entry


def raised(error: BaseException) -> BaseException:
    try:
        raise error
    except BaseException as caught:
        return caught


def test_structlog_handler_logs_a_non_sensitive_exception_with_its_traceback() -> None:
    error = raised(RuntimeError("operation failed for resource 7"))

    entry = log_one(RefreshEvent(EventKind.RESOURCE_REFRESH_FAILED, error=error))

    exception = entry["exception"]
    assert exception.startswith("Traceback (most recent call last):\n")
    assert "in raised" in exception
    assert exception.endswith("RuntimeError: operation failed for resource 7")
    assert "exc_info" not in entry


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("login failed: password=hunter2 user=bob", "password=[REDACTED] user=bob"),
        ("api_key=abc123&page=2", "api_key=[REDACTED]&page=2"),
        ("refresh token: 'tok-9' expired", "token: '[REDACTED]' expired"),
        (
            "cannot connect to postgresql://app:s3cret@db:5432/main",
            "postgresql://[REDACTED]@db:5432/main",
        ),
        (
            "header Authorization: Bearer eyJ.abc",
            "Authorization: [REDACTED] [REDACTED]",
        ),
        ("{'secret': 'x', 'mode': 'full'}", "{'secret': '[REDACTED]', 'mode': 'full'}"),
        ('{"accessToken": "x y"}', '{"accessToken": "[REDACTED]"}'),
    ],
)
def test_structlog_handler_redacts_credentials_in_exception_messages(
    message: str, expected: str
) -> None:
    secret_free = log_one(
        RefreshEvent(EventKind.REFRESH_FAILED, error=raised(RuntimeError(message)))
    )["exception"]

    assert secret_free.endswith(expected)
    assert "\nRuntimeError: " in secret_free
    for secret in ("hunter2", "abc123", "tok-9", "s3cret", "eyJ.abc", "x y"):
        assert secret not in secret_free


def test_structlog_handler_redacts_every_exception_in_a_chain() -> None:
    try:
        try:
            raise OSError("dsn=postgresql://u:pw1@h/db")
        except OSError as inner:
            raise RuntimeError("retry with token=pw2 failed") from inner
    except RuntimeError as outer:
        error = outer
    group = ExceptionGroup("several", [ValueError("password: pw3")])
    error.add_note("note with secret=pw4")

    chained = log_one(RefreshEvent(EventKind.REFRESH_FAILED, error=error))
    grouped = log_one(RefreshEvent(EventKind.REFRESH_FAILED, error=group))

    text = chained["exception"] + grouped["exception"]
    assert "OSError: dsn=[REDACTED]" in text
    assert "The above exception was the direct cause" in text
    assert "RuntimeError: retry with token=[REDACTED] failed" in text
    assert "note with secret=[REDACTED]" in text
    assert "ValueError: password: [REDACTED]" in text
    for secret in ("pw1", "pw2", "pw3", "pw4"):
        assert secret not in text


def test_structlog_handler_uses_a_custom_exception_redactor() -> None:
    seen: list[BaseException] = []

    def summarize(error: BaseException) -> str:
        seen.append(error)
        return type(error).__name__

    error = RuntimeError("password=hunter2")
    entry = log_one(
        RefreshEvent(EventKind.REFRESH_FAILED, error=error),
        exception_redactor=summarize,
    )

    assert entry["exception"] == "RuntimeError"
    assert seen == [error]


@pytest.mark.parametrize(
    "broken",
    [
        lambda error: (_ for _ in ()).throw(RuntimeError(f"echo {error}")),
        lambda error: cast(Any, error),
    ],
    ids=["raises", "not-text"],
)
def test_a_failing_exception_redactor_never_exposes_the_message(broken: Any) -> None:
    error = KeyError("password=hunter2")

    entry = log_one(
        RefreshEvent(EventKind.REFRESH_FAILED, error=error), exception_redactor=broken
    )

    assert entry["exception"] == "KeyError: [message withheld: redaction failed]"
    assert "hunter2" not in repr(entry)


def test_structlog_handler_attaches_raw_exceptions_only_when_asked() -> None:
    error = RuntimeError("password=hunter2")

    entry = log_one(
        RefreshEvent(EventKind.RESOURCE_REFRESH_FAILED, error=error),
        raw_exceptions=True,
    )

    assert entry["exc_info"] is error
    assert "exception" not in entry


def test_structlog_handler_rejects_a_non_boolean_raw_exceptions() -> None:
    with pytest.raises(
        ConfigurationError, match=r"^raw_exceptions must be a bool, not str$"
    ):
        StructlogEventHandler(raw_exceptions=cast(Any, "false"))


def test_structlog_handler_omits_the_exception_without_an_error() -> None:
    entry = log_one(RefreshEvent(EventKind.REFRESH_FAILED))

    assert "exception" not in entry
    assert "exc_info" not in entry


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("nothing to see here", "nothing to see here"),
        ("mode=full status: ok", "mode=full status: ok"),
        ("https://example.com/path?a=1", "https://example.com/path?a=1"),
        ("https://user@example.com", "https://[REDACTED]@example.com"),
        ("Basic dXNlcjpwYXNz", "Basic [REDACTED]"),
        ("client_secret = abc; next", "client_secret = [REDACTED]; next"),
        ("DB_PASSWORD='p w'", "DB_PASSWORD='[REDACTED]'"),
    ],
)
def test_redact_text(text: str, expected: str) -> None:
    assert redact_text(text) == expected


def test_redact_exception_without_a_traceback() -> None:
    assert redact_exception(ValueError("token=abc")) == "ValueError: token=[REDACTED]"


async def test_stdlib_failure_logs_are_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="refresh_engine")
    publisher = AsyncEventPublisher()

    async def failing(item: RefreshEvent) -> None:
        raise RuntimeError("password=hunter2")

    publisher.subscribe(failing)
    publisher.publish(RefreshEvent(EventKind.REFRESH_STARTED))
    await publisher.drain()

    (record,) = [item for item in caplog.records if item.name == "refresh_engine"]
    assert record.getMessage().endswith("RuntimeError: password=[REDACTED]")
    assert "hunter2" not in record.getMessage()
    assert record.exc_info is None


def test_stdlib_failure_logs_withhold_text_when_redaction_fails(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from refresh_engine._internal import redaction

    def broken(error: BaseException) -> str:
        raise RuntimeError("broken")

    monkeypatch.setattr(redaction, "redact_exception", broken)
    caplog.set_level(logging.ERROR, logger="refresh_engine")

    redaction.log_failure(
        logging.getLogger("refresh_engine"),
        logging.ERROR,
        "failed %s",
        "step",
        error=ValueError("password=hunter2"),
    )

    assert caplog.records[-1].getMessage() == (
        "failed step\nValueError: [message withheld: redaction failed]"
    )


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
        asyncio.run(
            handler(RefreshEvent(EventKind.REFRESH_COMPLETED, refresh_id="r-1"))
        )
        asyncio.run(handler(RefreshEvent(EventKind.RESOURCE_REFRESH_STARTED)))

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


def test_metrics_handler_requires_a_metrics_sink() -> None:
    with pytest.raises(TypeError, match="sink must subclass MetricsSink"):
        MetricsEventHandler(cast(Any, object()))


@pytest.mark.parametrize("prefix", ["", None, 1])
def test_metrics_handler_requires_a_non_empty_prefix(prefix: object) -> None:
    with pytest.raises(
        ConfigurationError, match=f"^{re.escape('prefix must be a non-empty string')}$"
    ):
        MetricsEventHandler(InMemoryMetrics(), prefix=cast(Any, prefix))


async def test_metrics_handler_reports_to_metrics_sink() -> None:
    calls: list[tuple[str, str, float, Mapping[str, str] | None]] = []

    class Sink(MetricsSink):
        async def increment(
            self,
            name: str,
            value: int = 1,
            attributes: Mapping[str, str] | None = None,
            /,
        ) -> None:
            calls.append(("increment", name, value, attributes))

        async def observe(
            self,
            name: str,
            value: float,
            attributes: Mapping[str, str] | None = None,
            /,
        ) -> None:
            calls.append(("observe", name, value, attributes))

    handler = MetricsEventHandler(Sink())
    await handler(RefreshEvent(EventKind.SCHEDULER_RUN_FAILED))
    await handler(
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

    assert isinstance(metrics, MetricsSink)
    assert isinstance(metrics, MetricsSink)


def test_in_memory_metrics_starts_empty() -> None:
    assert InMemoryMetrics().snapshot() == ({}, {})


async def test_in_memory_metrics_keys_sort_attributes_by_name() -> None:
    metrics = InMemoryMetrics()

    await metrics.increment("requests", 1, {"b": "2", "a": "1"})
    await metrics.observe("latency", 0.5, {"route": "/", "method": "GET"})

    counters, observations = metrics.snapshot()
    assert counters == {"requests{a=1,b=2}": 1}
    assert observations == {"latency{method=GET,route=/}": (0.5,)}


async def test_in_memory_metrics_uses_the_bare_name_without_attributes() -> None:
    metrics = InMemoryMetrics()

    await metrics.increment("requests")
    await metrics.increment("requests", 2, {})
    await metrics.increment("requests", 3, None)

    counters, _ = metrics.snapshot()
    assert counters == {"requests": 6}


async def test_in_memory_metrics_keeps_observations_in_order() -> None:
    metrics = InMemoryMetrics()

    await metrics.observe("latency", 0.5)
    await metrics.observe("latency", 1.5)
    await metrics.increment("latency")

    counters, observations = metrics.snapshot()
    assert observations == {"latency": (0.5, 1.5)}
    assert counters == {"latency": 1}


async def test_in_memory_metrics_snapshots_are_immutable_copies() -> None:
    metrics = InMemoryMetrics()
    await metrics.increment("requests")
    await metrics.observe("latency", 0.5)

    counters, observations = metrics.snapshot()
    await metrics.increment("requests")
    await metrics.observe("latency", 1.0)

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

    async def work() -> None:
        barrier.wait(timeout=5)
        for _ in range(iterations):
            await metrics.increment("requests", 1, {"worker": "any"})
            await metrics.observe("latency", 1.0)

    def run_work() -> None:
        asyncio.run(work())

    threads = [threading.Thread(target=run_work) for _ in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    counters, observations = metrics.snapshot()
    assert counters == {"requests{worker=any}": threads_count * iterations}
    assert len(observations["latency"]) == threads_count * iterations
