"""Bounded, retrying, dependency-ordered execution of a plan."""

from __future__ import annotations

import asyncio
from heapq import heappop, heappush
from time import perf_counter

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from refresh_engine._internal.concurrency import run_bounded, run_workers
from refresh_engine._internal.context import RunContext, describe
from refresh_engine._internal.observation import ObservationError, Observer, bounded
from refresh_engine._internal.planning import Plan
from refresh_engine._internal.values import utc_now
from refresh_engine.contracts import RefreshOperation
from refresh_engine.errors import DependencyCycleError
from refresh_engine.events import EventKind
from refresh_engine.models import (
    PlanAction,
    RefreshConfig,
    RefreshIssue,
    RefreshPhase,
    Resource,
    ResourceExecutionResult,
    ResourceSnapshot,
)


class Executor:
    """Run the operations of a plan."""

    def __init__(
        self,
        context: RunContext,
        plan: Plan,
        operation: RefreshOperation,
        config: RefreshConfig,
        observer: Observer,
    ) -> None:
        self._context = context
        self._plan = plan
        self._operation = operation
        self._limit = config.max_concurrency
        self._timeout = config.timeout.operation
        self._observer = observer
        retry = config.retry
        self._retrying = (
            None
            if retry.max_attempts == 1
            else AsyncRetrying(
                stop=stop_after_attempt(retry.max_attempts),
                wait=wait_exponential_jitter(
                    initial=retry.initial_delay,
                    max=retry.max_delay,
                    exp_base=retry.exponential_base,
                    jitter=retry.jitter,
                ),
                retry=retry_if_exception_type(retry.retryable_exceptions),
                reraise=True,
            )
        )
        self._finished = 0
        self._in_flight = 0

    async def run(self) -> None:
        """Run every operation that the plan and the failure policy allow."""
        context = self._context
        count = len(self._plan.resource_ids)
        if count == 0:
            return
        context.executions = [None] * count
        context.emit(EventKind.EXECUTION_STARTED, {"operations": count})
        started = perf_counter()
        dependents, indegree = self._plan.dependents, self._plan.indegree
        if dependents is None or indegree is None:
            await run_bounded(
                range(count), self._run_item, self._limit, context.should_stop
            )
        else:
            await self._run_graph(dependents, indegree)
        results = [result for result in context.executions if result is not None]
        not_started = count - len(results)
        if not_started:
            context.warnings.append(
                f"{not_started} planned operations were not started because the "
                "failure policy is fail_fast"
            )
        succeeded = sum(result.succeeded for result in results)
        context.emit(
            EventKind.EXECUTION_COMPLETED,
            {
                "succeeded": succeeded,
                "failed": len(results) - succeeded,
                "duration": perf_counter() - started,
            },
        )

    async def _run_graph(
        self, dependents: list[list[int]], indegree: list[int]
    ) -> None:
        plan = self._plan
        count = len(plan.resource_ids)
        remaining = list(indegree)
        done = [False] * count
        failed_dependency: dict[int, str] = {}
        ready: list[int] = []
        wakeup = asyncio.Event()

        def settle(index: int, *, succeeded: bool) -> None:
            pending = [(index, succeeded)]
            while pending:
                current, ok = pending.pop()
                for dependent in dependents[current]:
                    if done[dependent]:
                        continue
                    if not ok:
                        failed_dependency.setdefault(
                            dependent, plan.resource_ids[current]
                        )
                    remaining[dependent] -= 1
                    if remaining[dependent] == 0:
                        blocker = failed_dependency.get(dependent)
                        if blocker is None:
                            heappush(ready, dependent)
                        else:
                            self._skip_blocked(dependent, blocker)
                            done[dependent] = True
                            self._finished += 1
                            pending.append((dependent, False))

        for index in range(count):
            if remaining[index] == 0:
                heappush(ready, index)
        for index in sorted(plan.cycles):
            self._skip_cycle(index, plan.cycles[index])
            done[index] = True
            self._finished += 1
        for index in sorted(plan.cycles):
            settle(index, succeeded=False)

        async def worker() -> None:
            while True:
                while not ready:
                    if (
                        self._finished == count
                        or self._in_flight == 0
                        or self._context.should_stop()
                    ):
                        return
                    wakeup.clear()
                    await wakeup.wait()
                if self._context.should_stop():
                    return
                index = heappop(ready)
                self._in_flight += 1
                try:
                    succeeded = await self._run_item(index)
                finally:
                    self._in_flight -= 1
                done[index] = True
                self._finished += 1
                settle(index, succeeded=succeeded)
                wakeup.set()

        await run_workers(worker, min(self._limit, count))

    async def _run_item(self, index: int) -> bool:
        context = self._context
        resource_id = self._plan.resource_ids[index]
        action = self._plan.actions[index]
        started = perf_counter()
        if context.emit.enabled:
            context.emit(
                EventKind.RESOURCE_REFRESH_STARTED,
                {"action": action.value},
                resource_id=resource_id,
            )
        snapshot: ResourceSnapshot | None = None
        if action is PlanAction.REFRESH:
            observation = context.observations[resource_id]
            resource = observation.resource
            snapshot = context.snapshots.pop(resource_id, None)
            if snapshot is None:
                try:
                    snapshot, fresh = await self._observer.observe(
                        resource, observation.previous
                    )
                except ObservationError as failure:
                    issue = context.fail_resource(
                        resource_id,
                        failure.phase,
                        describe(failure.error),
                        type(failure.error).__name__,
                    )
                    self._record(index, started, 0, issue, failure.error)
                    return False
                fresh.change = observation.change
                context.observations[resource_id] = fresh
        else:
            resource = Resource(resource_id)
        attempts, error = await self._call(resource, snapshot, action)
        if error is None:
            if action is PlanAction.REFRESH:
                context.refreshed_at[resource_id] = utc_now()
            else:
                context.deletions_applied.append(resource_id)
            self._record(index, started, attempts, None, None)
            return True
        issue = context.fail_resource(
            resource_id, RefreshPhase.EXECUTION, describe(error), type(error).__name__
        )
        self._record(index, started, attempts, issue, error)
        return False

    async def _call(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
    ) -> tuple[int, Exception | None]:
        if self._retrying is None:
            try:
                await self._attempt(resource, snapshot, action)
            except Exception as error:
                return 1, error
            return 1, None
        attempts = 0
        try:
            async for attempt in self._retrying.copy():
                with attempt:
                    attempts = attempt.retry_state.attempt_number
                    await self._attempt(resource, snapshot, action)
        except Exception as error:
            return attempts, error
        return attempts, None

    async def _attempt(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
    ) -> None:
        await bounded(
            self._operation(resource, snapshot, action, self._context.request),
            self._timeout,
            "operation",
        )

    def _record(
        self,
        index: int,
        started: float,
        attempts: int,
        issue: RefreshIssue | None,
        error: Exception | None,
    ) -> None:
        context = self._context
        resource_id = self._plan.resource_ids[index]
        action = self._plan.actions[index]
        duration = perf_counter() - started
        context.executions[index] = ResourceExecutionResult(
            resource_id=resource_id,
            action=action,
            succeeded=issue is None,
            attempts=attempts,
            duration=duration,
            error=issue,
        )
        if not context.emit.enabled:
            return
        attributes: dict[str, object] = {
            "action": action.value,
            "attempts": attempts,
            "duration": duration,
        }
        if issue is None:
            context.emit(
                EventKind.RESOURCE_REFRESH_COMPLETED,
                attributes,
                resource_id=resource_id,
            )
        else:
            attributes["phase"] = issue.phase.value
            attributes["error_type"] = issue.exception_type
            context.emit(
                EventKind.RESOURCE_REFRESH_FAILED,
                attributes,
                resource_id=resource_id,
                error=error,
            )

    def _skip_cycle(self, index: int, error: DependencyCycleError) -> None:
        issue = self._context.fail_resource(
            self._plan.resource_ids[index],
            RefreshPhase.PLANNING,
            str(error),
            type(error).__name__,
        )
        self._skip(index, issue, "dependency_cycle")

    def _skip_blocked(self, index: int, blocker: str) -> None:
        issue = self._context.fail_resource(
            self._plan.resource_ids[index],
            RefreshPhase.EXECUTION,
            f"not run because dependency {blocker!r} failed",
            None,
        )
        self._skip(index, issue, "dependency_failed")

    def _skip(self, index: int, issue: RefreshIssue, reason: str) -> None:
        context = self._context
        resource_id = self._plan.resource_ids[index]
        action = self._plan.actions[index]
        context.executions[index] = ResourceExecutionResult(
            resource_id=resource_id,
            action=action,
            succeeded=False,
            attempts=0,
            duration=0.0,
            error=issue,
        )
        context.emit(
            EventKind.RESOURCE_REFRESH_SKIPPED,
            {"action": action.value, "reason": reason},
            resource_id=resource_id,
        )
