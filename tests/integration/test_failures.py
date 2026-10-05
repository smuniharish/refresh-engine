from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from typing import Any, cast

import pytest

from refresh_engine import (
    DiscoveryResult,
    EventKind,
    EventPublisher,
    FailurePolicy,
    FingerprintStrategyABC,
    InMemoryStateStore,
    PlanAction,
    PredicateSelector,
    RefreshConfig,
    RefreshEngine,
    RefreshEvent,
    RefreshMode,
    RefreshPhase,
    RefreshStatus,
    Resource,
    ResourceFingerprint,
    ResourceSnapshot,
    ResourceState,
    RetryConfig,
    StateStoreABC,
    StateStoreError,
    StateTransactionABC,
    TimeoutConfig,
)
from refresh_engine.events import EventPublisherABC
from tests.support import EventRecorder, FakeSource, RecordingOperation


class FailingCommitStore(InMemoryStateStore):
    """A store whose transactions fail to commit and optionally to roll back."""

    def __init__(self, *, rollback_error: Exception | None = None) -> None:
        super().__init__()
        self.rollbacks = 0
        self.rollback_error = rollback_error

    def transaction(self) -> StateTransactionABC:
        store = self

        class Transaction(StateTransactionABC):
            async def apply_changes(
                self,
                puts: Mapping[str, ResourceState],
                deletes: Any,
                /,
            ) -> None:
                raise StateStoreError("disk full")

            async def rollback(self) -> None:
                store.rollbacks += 1
                await super().rollback()
                if store.rollback_error is not None:
                    raise store.rollback_error

        return Transaction()


async def test_discovery_failure_fails_the_refresh_without_touching_state() -> None:
    source = FakeSource({"a": 1})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        before = await engine.store.load_all()
        source.discovery_error = ConnectionError("source unavailable")
        operation.calls.clear()
        result = await engine.refresh()
        after = await engine.store.load_all()
    assert result.status is RefreshStatus.FAILED
    (issue,) = result.errors
    assert issue.phase is RefreshPhase.DISCOVERY
    assert issue.message == "discovery failed: source unavailable"
    assert issue.exception_type == "ConnectionError"
    assert issue.resource_id is None
    assert (result.deleted_count, result.skipped_count) == (0, 0)
    assert operation.calls == []
    assert after == before


async def test_discovery_timeout_is_reported() -> None:
    class SlowSource(FakeSource):
        async def discover(self) -> DiscoveryResult:
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

    config = RefreshConfig(timeout=TimeoutConfig(discovery=0.01))
    async with RefreshEngine(
        SlowSource({}), RecordingOperation(), config=config
    ) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    assert result.errors[0].message == (
        "discovery failed: discovery timed out after 0.01 seconds"
    )
    assert result.errors[0].exception_type == "TimeoutError"


@pytest.mark.parametrize(
    ("discovered", "message"),
    [
        (
            "not a result",
            "discover() returned str; expected DiscoveryResult",
        ),
        (
            DiscoveryResult(cast(Any, ["a"])),
            "discovery yielded str; expected Resource",
        ),
        (
            DiscoveryResult([Resource("a"), Resource("a")]),
            "duplicate resource ID: 'a'",
        ),
    ],
)
async def test_invalid_discovery_results_are_rejected(
    discovered: object, message: str
) -> None:
    class InvalidSource(FakeSource):
        async def discover(self) -> DiscoveryResult:
            return cast(DiscoveryResult, discovered)

    async with RefreshEngine(InvalidSource({}), RecordingOperation()) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    assert result.errors[0].message == f"discovery failed: {message}"
    assert result.errors[0].exception_type == "DiscoveryError"


async def test_discovery_diagnostics_become_warnings() -> None:
    class DiagnosingSource(FakeSource):
        async def discover(self) -> DiscoveryResult:
            return DiscoveryResult([Resource("a")], diagnostics=("page 3 slow",))

    async with RefreshEngine(
        DiagnosingSource({"a": 1}), RecordingOperation()
    ) as engine:
        result = await engine.refresh()
    assert result.warnings == ("page 3 slow",)


async def test_state_load_failure_fails_the_refresh() -> None:
    class BrokenStore(StateStoreABC):
        async def load_all(self) -> Mapping[str, ResourceState]:
            raise StateStoreError("database offline")

        def transaction(self) -> StateTransactionABC:
            raise AssertionError("unreachable")

    async with RefreshEngine(
        FakeSource({"a": 1}), RecordingOperation(), store=BrokenStore()
    ) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    (issue,) = result.errors
    assert issue.phase is RefreshPhase.PERSISTENCE
    assert issue.message == "failed to load state: database offline"


async def test_snapshot_failure_keeps_previous_state_and_other_resources() -> None:
    source = FakeSource({"a": 1, "b": 1})
    events = EventPublisher()
    recorder = EventRecorder()
    events.subscribe(recorder)
    async with RefreshEngine(source, RecordingOperation(), events=events) as engine:
        await engine.refresh()
        before = (await engine.store.load_all())["a"]
        source.values = {"a": 2, "b": 2}
        source.snapshot_errors["a"] = OSError("read failed")
        result = await engine.refresh()
        states = await engine.store.load_all()
    assert result.status is RefreshStatus.PARTIAL
    (issue,) = result.errors
    assert (issue.phase, issue.resource_id, issue.message) == (
        RefreshPhase.SNAPSHOT,
        "a",
        "read failed",
    )
    assert (result.discovered_count, result.modified_count) == (2, 1)
    assert result.skipped_count == 0
    assert states["a"] == before
    (failure,) = recorder.of(EventKind.RESOURCE_SNAPSHOT_FAILED)
    assert failure.resource_id == "a"
    assert failure.attributes == {"phase": "snapshot", "error_type": "OSError"}
    assert isinstance(failure.error, OSError)


async def test_snapshot_failure_of_every_resource_fails_the_refresh() -> None:
    source = FakeSource({"a": 1}, snapshot_errors={"a": OSError()})
    async with RefreshEngine(source, RecordingOperation()) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    assert result.errors[0].message == "OSError"


async def test_snapshot_timeout_is_reported_per_resource() -> None:
    source = FakeSource({"a": 1}, snapshot_delay=10)
    config = RefreshConfig(timeout=TimeoutConfig(snapshot=0.01))
    async with RefreshEngine(source, RecordingOperation(), config=config) as engine:
        result = await engine.refresh()
    assert result.errors[0].message == "snapshot timed out after 0.01 seconds"
    assert result.errors[0].exception_type == "TimeoutError"


@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        ("text", "snapshot() returned str; expected ResourceSnapshot"),
        (
            ResourceSnapshot("other"),
            "snapshot() returned resource ID 'other' for 'a'",
        ),
    ],
)
async def test_snapshot_contract_violations_are_reported(
    snapshot: object, message: str
) -> None:
    class InvalidSource(FakeSource):
        async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
            return cast(ResourceSnapshot, snapshot)

    async with RefreshEngine(InvalidSource({"a": 1}), RecordingOperation()) as engine:
        result = await engine.refresh()
    (issue,) = result.errors
    assert (issue.phase, issue.message, issue.exception_type) == (
        RefreshPhase.SNAPSHOT,
        message,
        "SnapshotError",
    )


@pytest.mark.parametrize(
    ("outcome", "message", "exception_type"),
    [
        (
            ValueError("bad content"),
            "bad content",
            "ValueError",
        ),
        (
            "digest",
            "fingerprint() returned str; expected ResourceFingerprint",
            "FingerprintError",
        ),
    ],
)
async def test_fingerprint_failures_are_reported(
    outcome: object, message: str, exception_type: str
) -> None:
    class BrokenStrategy(FingerprintStrategyABC):
        async def fingerprint(
            self, snapshot: ResourceSnapshot, /
        ) -> ResourceFingerprint:
            if isinstance(outcome, Exception):
                raise outcome
            return cast(ResourceFingerprint, outcome)

    async with RefreshEngine(
        FakeSource({"a": 1}), RecordingOperation(), fingerprint=BrokenStrategy()
    ) as engine:
        result = await engine.refresh()
    (issue,) = result.errors
    assert (issue.phase, issue.message, issue.exception_type) == (
        RefreshPhase.FINGERPRINT,
        message,
        exception_type,
    )


async def test_failed_operation_does_not_advance_state_and_is_retried() -> None:
    source = FakeSource({"a": "A"})
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        before = (await engine.store.load_all())["a"]
        source.values["a"] = "B"
        operation.failures["a"] = RuntimeError("write rejected")
        failed = await engine.refresh()
        unchanged = (await engine.store.load_all())["a"]
        operation.failures.clear()
        retried = await engine.refresh()
        after = (await engine.store.load_all())["a"]
    assert failed.status is RefreshStatus.FAILED
    (execution,) = failed.executions
    assert (execution.succeeded, execution.attempts, execution.action) == (
        False,
        1,
        PlanAction.REFRESH,
    )
    assert execution.error == failed.errors[0]
    assert failed.errors[0].phase is RefreshPhase.EXECUTION
    assert unchanged == before
    assert retried.modified_count == 1
    assert retried.refreshed_count == 1
    assert after.fingerprint != before.fingerprint


async def test_operation_timeout_is_reported() -> None:
    operation = RecordingOperation(delay=10)
    config = RefreshConfig(timeout=TimeoutConfig(operation=0.01))
    async with RefreshEngine(FakeSource({"a": 1}), operation, config=config) as engine:
        result = await engine.refresh()
    assert result.errors[0].message == "operation timed out after 0.01 seconds"


async def test_timeout_error_raised_by_an_operation_is_reported_as_is() -> None:
    operation = RecordingOperation(failures={"a": TimeoutError("upstream timeout")})
    config = RefreshConfig(timeout=TimeoutConfig(operation=5))
    async with RefreshEngine(FakeSource({"a": 1}), operation, config=config) as engine:
        result = await engine.refresh()
    assert result.errors[0].message == "upstream timeout"


async def test_operation_is_retried_until_it_succeeds() -> None:
    operation = RecordingOperation(
        failures={"a": OSError("flaky")}, failures_remaining={"a": 2}
    )
    config = RefreshConfig(retry=RetryConfig(max_attempts=3, initial_delay=0))
    async with RefreshEngine(FakeSource({"a": 1}), operation, config=config) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.SUCCESS
    assert result.executions[0].attempts == 3
    assert len(operation.calls) == 3


async def test_operation_failing_every_attempt_reports_the_attempt_count() -> None:
    operation = RecordingOperation(failures={"a": OSError("down")})
    config = RefreshConfig(retry=RetryConfig(max_attempts=2, initial_delay=0))
    async with RefreshEngine(FakeSource({"a": 1}), operation, config=config) as engine:
        result = await engine.refresh()
    assert result.executions[0].attempts == 2
    assert result.errors[0].message == "down"


async def test_non_retryable_errors_are_not_retried() -> None:
    operation = RecordingOperation(failures={"a": ValueError("invalid")})
    retry = RetryConfig(
        max_attempts=5, initial_delay=0, retryable_exceptions=(OSError,)
    )
    async with RefreshEngine(
        FakeSource({"a": 1}), operation, config=RefreshConfig(retry=retry)
    ) as engine:
        result = await engine.refresh()
    assert result.executions[0].attempts == 1
    assert len(operation.calls) == 1


async def test_dependents_of_a_failed_operation_are_skipped() -> None:
    source = FakeSource(
        {"a": 1, "b": 1, "c": 1, "x": 1}, dependencies={"b": {"a"}, "c": {"b"}}
    )
    operation = RecordingOperation(failures={"a": RuntimeError("boom")})
    events = EventPublisher()
    recorder = EventRecorder()
    events.subscribe(recorder)
    async with RefreshEngine(source, operation, events=events) as engine:
        result = await engine.refresh()
        states = await engine.store.load_all()
    assert result.status is RefreshStatus.PARTIAL
    assert sorted(operation.ids()) == ["a", "x"]
    executions = {execution.resource_id: execution for execution in result.executions}
    assert executions["b"].attempts == 0
    assert executions["c"].error is not None
    assert executions["c"].error.message == "not run because dependency 'b' failed"
    assert [
        event.resource_id for event in recorder.of(EventKind.RESOURCE_REFRESH_SKIPPED)
    ] == [
        "b",
        "c",
    ]
    assert recorder.of(EventKind.RESOURCE_REFRESH_SKIPPED)[0].attributes == {
        "action": "refresh",
        "reason": "dependency_failed",
    }
    assert set(states) == {"x"}


async def test_fail_fast_stops_after_a_snapshot_failure_and_persists_nothing() -> None:
    source = FakeSource({"a": 1, "b": 2}, snapshot_errors={"a": OSError("read")})
    operation = RecordingOperation()
    config = RefreshConfig(failure_policy=FailurePolicy.FAIL_FAST, max_concurrency=1)
    async with RefreshEngine(source, operation, config=config) as engine:
        result = await engine.refresh()
        states = await engine.store.load_all()
    assert result.status is RefreshStatus.FAILED
    assert source.snapshot_calls == ["a"]
    assert operation.calls == []
    assert states == {}
    assert result.skipped_count == 0


async def test_fail_fast_stops_starting_operations_after_a_failure() -> None:
    source = FakeSource({f"r{index}": index for index in range(6)})
    operation = RecordingOperation(failures={"r1": RuntimeError("boom")})
    config = RefreshConfig(failure_policy=FailurePolicy.FAIL_FAST, max_concurrency=1)
    async with RefreshEngine(source, operation, config=config) as engine:
        result = await engine.refresh()
        states = await engine.store.load_all()
    assert result.status is RefreshStatus.FAILED
    assert operation.ids() == ["r0", "r1"]
    assert len(result.executions) == 2
    assert result.warnings == (
        "4 planned operations were not started because the failure policy is fail_fast",
    )
    assert states == {}


async def test_fail_fast_with_a_dependency_cycle_runs_nothing() -> None:
    source = FakeSource({"a": 1, "b": 1, "c": 1}, dependencies={"a": {"b"}, "b": {"a"}})
    operation = RecordingOperation()
    config = RefreshConfig(failure_policy=FailurePolicy.FAIL_FAST)
    async with RefreshEngine(source, operation, config=config) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    assert operation.calls == []
    assert len(result.errors) == 2


async def test_overall_timeout_fails_the_refresh_but_keeps_completed_work() -> None:
    source = FakeSource({"fast": 1, "slow": 2})

    async def operation(
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: object,
        /,
    ) -> None:
        if resource.resource_id == "slow":
            await asyncio.sleep(10)

    config = RefreshConfig(timeout=TimeoutConfig(overall=0.2))
    async with RefreshEngine(source, operation, config=config) as engine:
        result = await engine.refresh()
        states = await engine.store.load_all()
    assert result.status is RefreshStatus.FAILED
    (issue,) = result.errors
    assert issue.phase is RefreshPhase.EXECUTION
    assert issue.message == "the refresh exceeded the overall timeout of 0.2 seconds"
    assert issue.exception_type == "TimeoutError"
    assert result.refreshed_count == 1
    assert set(states) == {"fast"}


async def test_overall_timeout_with_fail_fast_persists_nothing() -> None:
    source = FakeSource({"a": 1}, snapshot_delay=10)
    config = RefreshConfig(
        failure_policy=FailurePolicy.FAIL_FAST,
        timeout=TimeoutConfig(overall=0.01),
    )
    async with RefreshEngine(source, RecordingOperation(), config=config) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    assert result.errors[0].phase is RefreshPhase.SNAPSHOT


async def test_commit_failure_fails_the_refresh_and_rolls_back() -> None:
    store = FailingCommitStore()
    operation = RecordingOperation()
    async with RefreshEngine(FakeSource({"a": 1}), operation, store=store) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    assert result.refreshed_count == 1
    assert len(result.executions) == 1
    (issue,) = result.errors
    assert issue.phase is RefreshPhase.PERSISTENCE
    assert issue.message == "failed to commit state: disk full"
    assert store.rollbacks == 1


async def test_rollback_errors_after_a_failed_commit_are_suppressed() -> None:
    store = FailingCommitStore(rollback_error=RuntimeError("rollback failed"))
    async with RefreshEngine(
        FakeSource({"a": 1}), RecordingOperation(), store=store
    ) as engine:
        result = await engine.refresh()
    assert result.errors[0].message == "failed to commit state: disk full"
    assert store.rollbacks == 1


async def test_selector_failure_fails_the_refresh() -> None:
    def explode(resource: Resource) -> bool:
        raise KeyError("tags")

    async with RefreshEngine(FakeSource({"a": 1}), RecordingOperation()) as engine:
        result = await engine.refresh(selector=PredicateSelector(explode))
    (issue,) = result.errors
    assert issue.phase is RefreshPhase.PLANNING
    assert issue.message == "the selector failed: 'tags'"


async def test_unexpected_errors_are_reported_as_refresh_failures() -> None:
    class CorruptStore(InMemoryStateStore):
        async def load_all(self) -> Mapping[str, ResourceState]:
            return cast(Mapping[str, ResourceState], None)

    async with RefreshEngine(
        FakeSource({"a": 1}), RecordingOperation(), store=CorruptStore()
    ) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    (issue,) = result.errors
    assert issue.phase is RefreshPhase.SNAPSHOT
    assert issue.exception_type == "AttributeError"


async def test_foreign_timeout_errors_are_not_the_overall_timeout() -> None:
    class TimingOutMapping(Mapping[str, ResourceState]):
        def __getitem__(self, key: str) -> ResourceState:
            raise TimeoutError("lookup timed out")

        def __iter__(self) -> Iterator[str]:
            return iter(())

        def __len__(self) -> int:
            return 0

        def get(self, key: str, default: object = None) -> Any:
            raise TimeoutError("lookup timed out")

    class TimingOutStore(InMemoryStateStore):
        async def load_all(self) -> Mapping[str, ResourceState]:
            return TimingOutMapping()

    config = RefreshConfig(timeout=TimeoutConfig(overall=60))
    async with RefreshEngine(
        FakeSource({"a": 1}),
        RecordingOperation(),
        store=TimingOutStore(),
        config=config,
    ) as engine:
        result = await engine.refresh()
    (issue,) = result.errors
    assert issue.message == "lookup timed out"
    assert issue.exception_type == "TimeoutError"


async def test_a_failing_event_publisher_never_breaks_a_refresh(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenPublisher(EventPublisherABC):
        def subscribe(self, handler: Any) -> None: ...

        def unsubscribe(self, handler: Any) -> None: ...

        def publish(self, event: RefreshEvent) -> None:
            raise RuntimeError("bus down")

        async def drain(self) -> None:
            return None

    async with RefreshEngine(
        FakeSource({"a": 1}), RecordingOperation(), events=BrokenPublisher()
    ) as engine:
        result = await engine.refresh()
    assert result.status is RefreshStatus.SUCCESS
    assert "failed to publish refresh_started event" in caplog.text


async def test_status_is_partial_when_some_resources_fail() -> None:
    operation = RecordingOperation(failures={"a": RuntimeError("x")})
    async with RefreshEngine(FakeSource({"a": 1, "b": 1}), operation) as engine:
        result = await engine.refresh(mode=RefreshMode.FULL)
    assert result.status is RefreshStatus.PARTIAL
    assert result.failed_count == 1


async def test_impacted_resource_that_cannot_be_reread_is_marked_for_retry() -> None:
    source = FakeSource(
        {"base": 1, "derived": 1}, dependencies={"derived": {"base"}}, cheap=True
    )
    operation = RecordingOperation()
    async with RefreshEngine(source, operation) as engine:
        await engine.refresh()
        operation.calls.clear()
        source.values["base"] = 2
        source.snapshot_errors["derived"] = OSError("gone away")
        result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
        marker = (await engine.store.load_all())["derived"]
    assert result.status is RefreshStatus.PARTIAL
    assert operation.ids() == ["base"]
    executions = {item.resource_id: item for item in result.executions}
    assert executions["derived"].attempts == 0
    assert executions["derived"].error is not None
    assert executions["derived"].error.phase is RefreshPhase.SNAPSHOT
    assert marker.fingerprint.algorithm == "refresh-engine:retry"


async def test_cancellation_during_commit_rolls_back_and_propagates() -> None:
    committing = asyncio.Event()
    rolled_back: list[bool] = []

    class SlowStore(InMemoryStateStore):
        def transaction(self) -> StateTransactionABC:
            class Transaction(StateTransactionABC):
                async def apply_changes(
                    self, puts: Mapping[str, ResourceState], deletes: Any, /
                ) -> None:
                    committing.set()
                    await asyncio.Event().wait()

                async def rollback(self) -> None:
                    rolled_back.append(True)
                    await super().rollback()

            return Transaction()

    engine = RefreshEngine(
        FakeSource({"a": 1}), RecordingOperation(), store=SlowStore()
    )
    task = asyncio.create_task(engine.refresh())
    await asyncio.wait_for(committing.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await engine.close()
    assert rolled_back == [True]
    assert await engine.store.load_all() == {}
