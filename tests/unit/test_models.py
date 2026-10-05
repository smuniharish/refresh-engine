"""Tests for the immutable models in `refresh_engine.models`."""

from __future__ import annotations

import dataclasses
import math
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

import pytest

from refresh_engine import (
    ConfigurationError,
    DiscoveryResult,
    FailurePolicy,
    IDSelector,
    OverlapPolicy,
    PlanAction,
    RefreshConfig,
    RefreshIssue,
    RefreshMode,
    RefreshPhase,
    RefreshRequest,
    RefreshResult,
    RefreshStatus,
    Resource,
    ResourceExecutionResult,
    ResourceFingerprint,
    ResourceSnapshot,
    ResourceState,
    RetryConfig,
    TimeoutConfig,
    TriggerSource,
)

FINGERPRINT = ResourceFingerprint("sha256:test", "abc123")
STARTED = datetime(2024, 1, 1, tzinfo=UTC)


def exactly(message: str) -> str:
    return f"^{re.escape(message)}$"


def make_result(errors: tuple[RefreshIssue, ...] = ()) -> RefreshResult:
    return RefreshResult(
        refresh_id="refresh-1",
        request=RefreshRequest(),
        status=RefreshStatus.SUCCESS,
        started_at=STARTED,
        completed_at=STARTED + timedelta(seconds=1),
        duration=1.0,
        errors=errors,
    )


class MatchEverything:
    def matches(self, resource: Resource, /) -> bool:
        return True


class NotASelector:
    matches = True


# Enumerations


@pytest.mark.parametrize(
    ("enum", "values"),
    [
        (RefreshMode, ["incremental", "full", "targeted", "dependency_aware"]),
        (TriggerSource, ["manual", "scheduled", "event", "query", "external"]),
        (
            OverlapPolicy,
            ["coalesce", "queue", "skip_if_running", "cancel_previous"],
        ),
        (FailurePolicy, ["best_effort", "fail_fast"]),
        (
            RefreshStatus,
            ["success", "partial", "failed", "cancelled", "skipped"],
        ),
        (PlanAction, ["refresh", "delete"]),
        (
            RefreshPhase,
            [
                "discovery",
                "snapshot",
                "fingerprint",
                "planning",
                "execution",
                "persistence",
            ],
        ),
    ],
    ids=lambda value: value.__name__ if isinstance(value, type) else None,
)
def test_enumerations_use_lowercase_string_values(
    enum: type[StrEnum], values: list[str]
) -> None:
    assert [member.value for member in enum] == values


# Resource


def test_resource_defaults() -> None:
    resource = Resource("a")

    assert resource.resource_id == "a"
    assert resource.metadata == {}
    assert resource.version is None
    assert resource.dependencies is None
    assert resource.cheap_indicator is None
    assert resource.cheap_indicator_reliable is False


@pytest.mark.parametrize("resource_id", ["", None, 1])
def test_resource_requires_a_non_empty_string_id(resource_id: object) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("resource_id must be a non-empty string")
    ):
        Resource(cast(Any, resource_id))


@pytest.mark.parametrize("field", ["version", "cheap_indicator"])
def test_resource_rejects_non_string_optional_text(field: str) -> None:
    options: dict[str, Any] = {field: 1}

    with pytest.raises(
        ConfigurationError, match=exactly(f"{field} must be a string or None")
    ):
        Resource("a", **options)


def test_resource_keeps_optional_text() -> None:
    resource = Resource("a", version="v1", cheap_indicator="etag-1")

    assert resource.version == "v1"
    assert resource.cheap_indicator == "etag-1"


def test_resource_metadata_is_an_independent_read_only_copy() -> None:
    metadata: dict[str, Any] = {"owner": "team-a"}

    resource = Resource("a", metadata=metadata)
    metadata["owner"] = "team-b"

    assert resource.metadata == {"owner": "team-a"}
    with pytest.raises(TypeError):
        cast(Any, resource.metadata)["owner"] = "team-c"


@pytest.mark.parametrize(
    ("dependencies", "expected"),
    [
        (["b", "c", "b"], frozenset({"b", "c"})),
        ({"b"}, frozenset({"b"})),
        ("bc", frozenset({"bc"})),
        ([], frozenset()),
        (None, None),
    ],
    ids=["list", "set", "single-string", "empty", "unknown"],
)
def test_resource_normalizes_dependencies(
    dependencies: object, expected: frozenset[str] | None
) -> None:
    resource = Resource("a", dependencies=cast(Any, dependencies))

    assert resource.dependencies == expected
    assert type(resource.dependencies) is type(expected)


def test_resource_rejects_invalid_dependency_ids() -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly("dependencies must contain only non-empty strings"),
    ):
        Resource("a", dependencies=cast(Any, ["b", ""]))


def test_resource_is_hashable_with_metadata() -> None:
    first = Resource("a", metadata={"tags": ["x"]}, dependencies={"b"})
    second = Resource("a", metadata={"tags": ["x"]}, dependencies={"b"})

    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1


def test_resource_metadata_takes_part_in_equality() -> None:
    assert Resource("a", metadata={"k": 1}) != Resource("a", metadata={"k": 2})


# ResourceSnapshot


def test_resource_snapshot_defaults() -> None:
    snapshot = ResourceSnapshot("a")

    assert snapshot.content is None
    assert snapshot.metadata == {}
    assert snapshot.version is None
    assert snapshot.version_token is None
    assert snapshot.etag is None
    assert snapshot.dependencies is None


def test_resource_snapshot_requires_a_non_empty_string_id() -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("resource_id must be a non-empty string")
    ):
        ResourceSnapshot("")


@pytest.mark.parametrize("field", ["version", "version_token", "etag"])
def test_resource_snapshot_rejects_non_string_optional_text(field: str) -> None:
    options: dict[str, Any] = {field: 1}

    with pytest.raises(
        ConfigurationError, match=exactly(f"{field} must be a string or None")
    ):
        ResourceSnapshot("a", **options)


def test_resource_snapshot_freezes_metadata_and_normalizes_dependencies() -> None:
    metadata: dict[str, Any] = {"size": 1}

    snapshot = ResourceSnapshot("a", metadata=metadata, dependencies=cast(Any, "b"))
    metadata["size"] = 2

    assert snapshot.metadata == {"size": 1}
    assert snapshot.dependencies == frozenset({"b"})
    with pytest.raises(TypeError):
        cast(Any, snapshot.metadata)["size"] = 3


def test_resource_snapshot_rejects_invalid_dependency_ids() -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly("dependencies must contain only non-empty strings"),
    ):
        ResourceSnapshot("a", dependencies=cast(Any, [1]))


def test_resource_snapshot_keeps_content_as_given() -> None:
    content = {"payload": [1, 2, 3]}

    assert ResourceSnapshot("a", content=content).content is content


def test_resource_snapshot_is_hashable_with_unhashable_content_and_metadata() -> None:
    first = ResourceSnapshot("a", content=[1], metadata={"k": [1]}, etag="e")
    second = ResourceSnapshot("a", content=[1], metadata={"k": [1]}, etag="e")

    assert first == second
    assert hash(first) == hash(second)


# DiscoveryResult


def test_discovery_result_defaults_and_keeps_resources() -> None:
    resources = [Resource("a")]

    result = DiscoveryResult(resources)

    assert result.resources is resources
    assert result.complete is True
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    ("diagnostics", "expected"),
    [
        ("listing was truncated", ("listing was truncated",)),
        (["first", "second"], ("first", "second")),
        (iter(["only"]), ("only",)),
        ((), ()),
    ],
    ids=["single-string", "list", "iterator", "empty"],
)
def test_discovery_result_normalizes_diagnostics_to_a_tuple(
    diagnostics: object, expected: tuple[str, ...]
) -> None:
    result = DiscoveryResult([], complete=False, diagnostics=cast(Any, diagnostics))

    assert result.diagnostics == expected
    assert result.complete is False


def test_discovery_result_rejects_non_string_diagnostics() -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("diagnostics must contain only strings")
    ):
        DiscoveryResult([], diagnostics=cast(Any, ["ok", 1]))


# ResourceFingerprint


def test_resource_fingerprint_keeps_algorithm_and_value() -> None:
    fingerprint = ResourceFingerprint("sha256:content", "deadbeef")

    assert fingerprint.algorithm == "sha256:content"
    assert fingerprint.value == "deadbeef"
    assert fingerprint == ResourceFingerprint("sha256:content", "deadbeef")
    assert fingerprint != ResourceFingerprint("sha256:metadata", "deadbeef")


@pytest.mark.parametrize(
    ("algorithm", "value", "message"),
    [
        ("", "v", "algorithm must be a non-empty string"),
        (1, "v", "algorithm must be a non-empty string"),
        ("alg", "", "value must be a non-empty string"),
        ("alg", None, "value must be a non-empty string"),
    ],
)
def test_resource_fingerprint_requires_non_empty_strings(
    algorithm: object, value: object, message: str
) -> None:
    with pytest.raises(ConfigurationError, match=exactly(message)):
        ResourceFingerprint(cast(Any, algorithm), cast(Any, value))


# ResourceState


def test_resource_state_defaults() -> None:
    before = datetime.now(UTC)
    state = ResourceState("a", FINGERPRINT)
    after = datetime.now(UTC)

    assert state.fingerprint is FINGERPRINT
    assert state.version is None
    assert state.cheap_indicator is None
    assert state.snapshot_metadata == {}
    assert state.dependencies == frozenset()
    assert state.refreshed_at.tzinfo is UTC
    assert before <= state.refreshed_at <= after


def test_resource_state_requires_a_non_empty_string_id() -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("resource_id must be a non-empty string")
    ):
        ResourceState("", FINGERPRINT)


@pytest.mark.parametrize(
    "fingerprint",
    [None, "sha256:abc", ("sha256", "abc")],
    ids=["none", "str", "tuple"],
)
def test_resource_state_requires_a_resource_fingerprint(fingerprint: object) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("fingerprint must be a ResourceFingerprint")
    ):
        ResourceState("a", cast(Any, fingerprint))


@pytest.mark.parametrize(
    ("dependencies", "expected"),
    [
        (["b", "c"], frozenset({"b", "c"})),
        ({"b"}, frozenset({"b"})),
        ("bc", frozenset({"bc"})),
    ],
    ids=["list", "set", "single-string"],
)
def test_resource_state_normalizes_dependencies_to_a_frozenset(
    dependencies: object, expected: frozenset[str]
) -> None:
    state = ResourceState("a", FINGERPRINT, dependencies=cast(Any, dependencies))

    assert state.dependencies == expected
    assert isinstance(state.dependencies, frozenset)


def test_resource_state_rejects_invalid_dependency_ids() -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly("dependencies must contain only non-empty strings"),
    ):
        ResourceState("a", FINGERPRINT, dependencies=cast(Any, [None]))


def test_resource_state_freezes_snapshot_metadata() -> None:
    metadata: dict[str, Any] = {"size": 1}

    state = ResourceState("a", FINGERPRINT, snapshot_metadata=metadata)
    metadata["size"] = 2

    assert state.snapshot_metadata == {"size": 1}
    with pytest.raises(TypeError):
        cast(Any, state.snapshot_metadata)["size"] = 3


def test_resource_state_is_hashable_with_snapshot_metadata() -> None:
    state = ResourceState(
        "a",
        FINGERPRINT,
        snapshot_metadata={"tags": ["x"]},
        refreshed_at=STARTED,
    )
    same = ResourceState(
        "a",
        FINGERPRINT,
        snapshot_metadata={"tags": ["x"]},
        refreshed_at=STARTED,
    )

    assert state == same
    assert hash(state) == hash(same)


# RefreshRequest


def test_refresh_request_defaults() -> None:
    before = datetime.now(UTC)
    request = RefreshRequest()
    after = datetime.now(UTC)

    assert request.mode is RefreshMode.INCREMENTAL
    assert request.resource_ids == frozenset()
    assert request.selector is None
    assert request.trigger is TriggerSource.MANUAL
    assert request.metadata == {}
    assert request.correlation_id is None
    assert request.requested_at.tzinfo is UTC
    assert before <= request.requested_at <= after


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("full", RefreshMode.FULL),
        ("incremental", RefreshMode.INCREMENTAL),
        ("dependency_aware", RefreshMode.DEPENDENCY_AWARE),
    ],
)
def test_refresh_request_coerces_mode_strings(
    value: str, expected: RefreshMode
) -> None:
    request = RefreshRequest(mode=cast(Any, value))

    assert request.mode is expected
    assert type(request.mode) is RefreshMode


def test_refresh_request_coerces_trigger_strings() -> None:
    request = RefreshRequest(trigger=cast(Any, "scheduled"))

    assert request.trigger is TriggerSource.SCHEDULED
    assert type(request.trigger) is TriggerSource


@pytest.mark.parametrize("value", ["partial", "FULL", 1, None])
def test_refresh_request_rejects_unknown_modes_and_lists_the_choices(
    value: object,
) -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly(
            "mode must be one of: incremental, full, targeted, dependency_aware"
        ),
    ) as caught:
        RefreshRequest(mode=cast(Any, value))

    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_refresh_request_rejects_unknown_triggers_and_lists_the_choices() -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly(
            "trigger must be one of: manual, scheduled, event, query, external"
        ),
    ):
        RefreshRequest(trigger=cast(Any, "cron"))


@pytest.mark.parametrize(
    ("resource_ids", "expected"),
    [
        (["a", "b", "a"], frozenset({"a", "b"})),
        ({"a"}, frozenset({"a"})),
        ("abc", frozenset({"abc"})),
    ],
    ids=["list", "set", "single-string"],
)
def test_refresh_request_normalizes_resource_ids(
    resource_ids: object, expected: frozenset[str]
) -> None:
    request = RefreshRequest(
        mode=RefreshMode.TARGETED, resource_ids=cast(Any, resource_ids)
    )

    assert request.resource_ids == expected
    assert isinstance(request.resource_ids, frozenset)


@pytest.mark.parametrize("resource_ids", [[""], ["a", 1], [None]])
def test_refresh_request_rejects_invalid_resource_ids(resource_ids: list[Any]) -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly("resource_ids must contain only non-empty strings"),
    ):
        RefreshRequest(mode=RefreshMode.TARGETED, resource_ids=cast(Any, resource_ids))


@pytest.mark.parametrize(
    "selector",
    [object(), NotASelector(), "a"],
    ids=["object", "non-callable-matches", "str"],
)
def test_refresh_request_requires_a_selector_with_a_matches_method(
    selector: object,
) -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("selector must provide a matches() method")
    ):
        RefreshRequest(mode=RefreshMode.TARGETED, selector=cast(Any, selector))


def test_targeted_request_accepts_a_protocol_selector() -> None:
    selector = MatchEverything()

    request = RefreshRequest(mode=RefreshMode.TARGETED, selector=selector)

    assert request.selector is selector
    assert request.resource_ids == frozenset()


def test_targeted_request_accepts_both_ids_and_a_selector() -> None:
    selector = IDSelector("b")

    request = RefreshRequest(
        mode=RefreshMode.TARGETED, resource_ids={"a"}, selector=selector
    )

    assert request.resource_ids == frozenset({"a"})
    assert request.selector is selector


@pytest.mark.parametrize("resource_ids", [frozenset(), []], ids=["default", "empty"])
def test_targeted_request_requires_resource_ids_or_a_selector(
    resource_ids: list[str] | frozenset[str],
) -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly("a targeted refresh requires resource_ids or a selector"),
    ):
        RefreshRequest(mode=RefreshMode.TARGETED, resource_ids=cast(Any, resource_ids))


@pytest.mark.parametrize(
    "mode",
    [RefreshMode.INCREMENTAL, RefreshMode.FULL, RefreshMode.DEPENDENCY_AWARE],
)
@pytest.mark.parametrize(
    "selection",
    [{"resource_ids": {"a"}}, {"selector": IDSelector("a")}],
    ids=["resource_ids", "selector"],
)
def test_non_targeted_requests_reject_resource_ids_and_selectors(
    mode: RefreshMode, selection: dict[str, Any]
) -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly(
            "resource_ids and selector are only valid for targeted refreshes"
        ),
    ):
        RefreshRequest(mode=mode, **selection)


def test_refresh_request_metadata_is_an_independent_read_only_copy() -> None:
    metadata: dict[str, Any] = {"reason": "deploy"}

    request = RefreshRequest(metadata=metadata)
    metadata["reason"] = "changed"

    assert request.metadata == {"reason": "deploy"}
    with pytest.raises(TypeError):
        cast(Any, request.metadata)["reason"] = "other"


def test_refresh_request_rejects_a_non_string_correlation_id() -> None:
    with pytest.raises(
        ConfigurationError, match=exactly("correlation_id must be a string or None")
    ):
        RefreshRequest(correlation_id=cast(Any, 42))


def test_refresh_request_is_hashable_with_metadata_and_selector() -> None:
    request = RefreshRequest(
        mode=RefreshMode.TARGETED,
        selector=IDSelector("a"),
        metadata={"tags": ["x"]},
        correlation_id="c-1",
        requested_at=STARTED,
    )
    same = RefreshRequest(
        mode=RefreshMode.TARGETED,
        selector=IDSelector("a"),
        metadata={"tags": ["x"]},
        correlation_id="c-1",
        requested_at=STARTED,
    )

    assert request == same
    assert hash(request) == hash(same)


# Results


def test_refresh_issue_defaults() -> None:
    issue = RefreshIssue(phase=RefreshPhase.SNAPSHOT, message="boom")

    assert issue.resource_id is None
    assert issue.exception_type is None


def test_resource_execution_result_defaults_to_no_error() -> None:
    execution = ResourceExecutionResult(
        resource_id="a",
        action=PlanAction.REFRESH,
        succeeded=True,
        attempts=1,
        duration=0.5,
    )

    assert execution.error is None


def test_refresh_result_count_defaults() -> None:
    result = make_result()

    assert result.discovered_count == 0
    assert result.added_count == 0
    assert result.modified_count == 0
    assert result.dependency_changed_count == 0
    assert result.unchanged_count == 0
    assert result.deleted_count == 0
    assert result.impacted_count == 0
    assert result.refreshed_count == 0
    assert result.skipped_count == 0
    assert result.errors == ()
    assert result.warnings == ()
    assert result.executions == ()


@pytest.mark.parametrize("count", [0, 1, 3])
def test_refresh_result_failed_count_equals_the_number_of_errors(count: int) -> None:
    errors = tuple(
        RefreshIssue(
            phase=RefreshPhase.EXECUTION, message="failed", resource_id=f"r{index}"
        )
        for index in range(count)
    )

    assert make_result(errors).failed_count == count


# RetryConfig


def test_retry_config_defaults() -> None:
    config = RetryConfig()

    assert config.max_attempts == 1
    assert config.initial_delay == 0.1
    assert config.max_delay == 5.0
    assert config.exponential_base == 2.0
    assert config.jitter == 0.0
    assert config.retryable_exceptions == (Exception,)


def test_retry_config_accepts_boundary_values() -> None:
    config = RetryConfig(
        max_attempts=1,
        initial_delay=0,
        max_delay=0,
        exponential_base=1,
        jitter=0,
    )

    assert config.initial_delay == 0
    assert config.exponential_base == 1


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_attempts", 0, "max_attempts must be an integer of at least 1"),
        ("max_attempts", True, "max_attempts must be an integer of at least 1"),
        ("max_attempts", 2.0, "max_attempts must be an integer of at least 1"),
        ("initial_delay", -0.1, "initial_delay must be at least 0"),
        ("initial_delay", math.inf, "initial_delay must be a finite number"),
        ("initial_delay", "1", "initial_delay must be a finite number"),
        ("max_delay", -1, "max_delay must be at least 0"),
        ("max_delay", math.nan, "max_delay must be a finite number"),
        ("exponential_base", 0.5, "exponential_base must be at least 1"),
        ("exponential_base", True, "exponential_base must be a finite number"),
        ("jitter", -0.5, "jitter must be at least 0"),
        ("jitter", None, "jitter must be a finite number"),
    ],
)
def test_retry_config_rejects_invalid_values(
    field: str, value: object, message: str
) -> None:
    options: dict[str, Any] = {field: value}

    with pytest.raises(ConfigurationError, match=exactly(message)):
        RetryConfig(**options)


def test_retry_config_normalizes_a_single_exception_type_to_a_tuple() -> None:
    config = RetryConfig(retryable_exceptions=cast(Any, TimeoutError))

    assert config.retryable_exceptions == (TimeoutError,)


def test_retry_config_normalizes_an_iterable_of_exception_types_to_a_tuple() -> None:
    config = RetryConfig(retryable_exceptions=cast(Any, [TimeoutError, KeyError]))

    assert config.retryable_exceptions == (TimeoutError, KeyError)


@pytest.mark.parametrize(
    "exceptions",
    [
        (),
        (KeyboardInterrupt,),
        BaseException,
        (ValueError("not a type"),),
        (int,),
        [ValueError, "KeyError"],
    ],
    ids=[
        "empty",
        "base-exception-subclass",
        "single-base-exception",
        "instance",
        "non-exception-type",
        "string",
    ],
)
def test_retry_config_requires_exception_subclasses(exceptions: object) -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly("retryable_exceptions must contain Exception subclasses"),
    ):
        RetryConfig(retryable_exceptions=cast(Any, exceptions))


# TimeoutConfig

TIMEOUT_FIELDS = ("discovery", "snapshot", "operation", "overall")


def test_timeout_config_disables_every_timeout_by_default() -> None:
    config = TimeoutConfig()

    assert [getattr(config, name) for name in TIMEOUT_FIELDS] == [None] * 4


def test_timeout_config_accepts_positive_finite_values() -> None:
    config = TimeoutConfig(discovery=1, snapshot=0.5, operation=2.0, overall=30)

    assert (config.discovery, config.snapshot, config.operation, config.overall) == (
        1,
        0.5,
        2.0,
        30,
    )


@pytest.mark.parametrize("field", TIMEOUT_FIELDS)
@pytest.mark.parametrize(
    ("value", "problem"),
    [
        (0, "must be greater than 0"),
        (-1, "must be greater than 0"),
        (math.inf, "must be a finite number"),
        (math.nan, "must be a finite number"),
        (True, "must be a finite number"),
        ("5", "must be a finite number"),
    ],
    ids=["zero", "negative", "inf", "nan", "bool", "str"],
)
def test_timeout_config_requires_none_or_positive_finite_values(
    field: str, value: object, problem: str
) -> None:
    options: dict[str, Any] = {field: value}

    with pytest.raises(ConfigurationError, match=exactly(f"timeout.{field} {problem}")):
        TimeoutConfig(**options)


# RefreshConfig


def test_refresh_config_defaults() -> None:
    config = RefreshConfig()

    assert config.max_concurrency == 10
    assert config.overlap_policy is OverlapPolicy.COALESCE
    assert config.failure_policy is FailurePolicy.BEST_EFFORT
    assert config.retry == RetryConfig()
    assert config.timeout == TimeoutConfig()


@pytest.mark.parametrize("value", [0, -1, True, 2.5, "4"])
def test_refresh_config_rejects_invalid_max_concurrency(value: object) -> None:
    with pytest.raises(
        ConfigurationError,
        match=exactly("max_concurrency must be an integer of at least 1"),
    ):
        RefreshConfig(max_concurrency=cast(Any, value))


def test_refresh_config_coerces_policy_strings() -> None:
    config = RefreshConfig(
        overlap_policy=cast(Any, "skip_if_running"),
        failure_policy=cast(Any, "fail_fast"),
    )

    assert config.overlap_policy is OverlapPolicy.SKIP_IF_RUNNING
    assert config.failure_policy is FailurePolicy.FAIL_FAST


@pytest.mark.parametrize(
    ("field", "message"),
    [
        (
            "overlap_policy",
            "overlap_policy must be one of: coalesce, queue, skip_if_running, "
            "cancel_previous",
        ),
        ("failure_policy", "failure_policy must be one of: best_effort, fail_fast"),
    ],
)
def test_refresh_config_rejects_unknown_policies(field: str, message: str) -> None:
    options: dict[str, Any] = {field: "unknown"}

    with pytest.raises(ConfigurationError, match=exactly(message)):
        RefreshConfig(**options)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("retry", {"max_attempts": 3}, "retry must be a RetryConfig"),
        ("retry", TimeoutConfig(), "retry must be a RetryConfig"),
        ("timeout", {"overall": 1.0}, "timeout must be a TimeoutConfig"),
        ("timeout", RetryConfig(), "timeout must be a TimeoutConfig"),
    ],
)
def test_refresh_config_requires_config_objects(
    field: str, value: object, message: str
) -> None:
    options: dict[str, Any] = {field: value}

    with pytest.raises(ConfigurationError, match=exactly(message)):
        RefreshConfig(**options)


def test_refresh_config_keeps_nested_configs() -> None:
    retry = RetryConfig(max_attempts=3)
    timeout = TimeoutConfig(overall=10)

    config = RefreshConfig(retry=retry, timeout=timeout)

    assert config.retry is retry
    assert config.timeout is timeout


# Immutability

FROZEN_MODELS: list[tuple[object, str]] = [
    (Resource("a"), "resource_id"),
    (ResourceSnapshot("a"), "content"),
    (DiscoveryResult([]), "complete"),
    (FINGERPRINT, "value"),
    (ResourceState("a", FINGERPRINT), "dependencies"),
    (RefreshRequest(), "mode"),
    (RefreshIssue(phase=RefreshPhase.PLANNING, message="m"), "message"),
    (
        ResourceExecutionResult(
            resource_id="a",
            action=PlanAction.DELETE,
            succeeded=False,
            attempts=0,
            duration=0.0,
        ),
        "attempts",
    ),
    (make_result(), "status"),
    (RetryConfig(), "max_attempts"),
    (TimeoutConfig(), "overall"),
    (RefreshConfig(), "max_concurrency"),
]


@pytest.mark.parametrize(
    ("model", "field"),
    FROZEN_MODELS,
    ids=[type(model).__name__ for model, _ in FROZEN_MODELS],
)
def test_models_are_frozen(model: object, field: str) -> None:
    original = getattr(model, field)

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(model, field, None)

    assert getattr(model, field) is original
