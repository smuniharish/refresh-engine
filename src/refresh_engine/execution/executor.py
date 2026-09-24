"""Bounded, retrying, dependency-ordered refresh execution."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from time import perf_counter

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from refresh_engine.api.protocols import RefreshOperation
from refresh_engine.core.models import (
    FailurePolicy,
    PlanAction,
    PlanItem,
    RefreshConfig,
    RefreshIssue,
    RefreshPlan,
    Resource,
    ResourceExecutionResult,
    ResourceSnapshot,
)
from refresh_engine.events import EventKind, EventPublisher, RefreshEvent


class RefreshExecutor:
    def __init__(
        self,
        operation: RefreshOperation,
        config: RefreshConfig,
        events: EventPublisher,
    ) -> None:
        self._operation = operation
        self._config = config
        self._events = events

    async def execute(
        self,
        plan: RefreshPlan,
        resources: Mapping[str, Resource],
        snapshots: Mapping[str, ResourceSnapshot],
    ) -> tuple[ResourceExecutionResult, ...]:
        semaphore = asyncio.Semaphore(self._config.max_concurrency)
        all_results: list[ResourceExecutionResult] = []
        await self._events.publish(
            RefreshEvent(EventKind.EXECUTION_STARTED, plan.refresh_id)
        )
        for level in plan.levels:
            tasks = [
                asyncio.create_task(
                    self._execute_item(plan, item, resources, snapshots, semaphore),
                    name=f"refresh:{plan.refresh_id}:{item.resource_id}",
                )
                for item in level
            ]
            try:
                level_results = await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            all_results.extend(level_results)
            if self._config.failure_policy is FailurePolicy.FAIL_FAST and any(
                not result.succeeded for result in level_results
            ):
                break
        await self._events.publish(
            RefreshEvent(EventKind.EXECUTION_COMPLETED, plan.refresh_id)
        )
        return tuple(all_results)

    async def _execute_item(
        self,
        plan: RefreshPlan,
        item: PlanItem,
        resources: Mapping[str, Resource],
        snapshots: Mapping[str, ResourceSnapshot],
        semaphore: asyncio.Semaphore,
    ) -> ResourceExecutionResult:
        async with semaphore:
            started = perf_counter()
            await self._events.publish(
                RefreshEvent(
                    EventKind.RESOURCE_REFRESH_STARTED,
                    plan.refresh_id,
                    item.resource_id,
                )
            )
            attempts = 0
            error: Exception | None = None
            retry = self._config.retry
            try:
                if retry.max_attempts == 1:
                    attempts = 1
                    await self._run_operation(plan, item, resources, snapshots)
                else:
                    async for attempt in AsyncRetrying(
                        stop=stop_after_attempt(retry.max_attempts),
                        wait=wait_exponential(
                            multiplier=retry.initial_delay,
                            max=retry.max_delay,
                            exp_base=retry.exponential_base,
                        )
                        + wait_random(0, retry.jitter),
                        retry=retry_if_exception_type(retry.retryable_exceptions),
                        reraise=True,
                    ):
                        attempts = attempt.retry_state.attempt_number
                        with attempt:
                            await self._run_operation(plan, item, resources, snapshots)
            except asyncio.CancelledError:
                raise
            except Exception as caught:
                error = caught
            else:
                await self._events.publish(
                    RefreshEvent(
                        EventKind.RESOURCE_REFRESH_COMPLETED,
                        plan.refresh_id,
                        item.resource_id,
                    )
                )
                return ResourceExecutionResult(
                    item.resource_id,
                    item.action,
                    True,
                    attempts,
                    perf_counter() - started,
                )
            assert error is not None
            issue = RefreshIssue(
                "execution",
                str(error),
                item.resource_id,
                type(error).__name__,
            )
            await self._events.publish(
                RefreshEvent(
                    EventKind.RESOURCE_REFRESH_FAILED,
                    plan.refresh_id,
                    item.resource_id,
                    attributes={"error_type": type(error).__name__},
                )
            )
            return ResourceExecutionResult(
                item.resource_id,
                item.action,
                False,
                attempts,
                perf_counter() - started,
                issue,
            )

    async def _run_operation(
        self,
        plan: RefreshPlan,
        item: PlanItem,
        resources: Mapping[str, Resource],
        snapshots: Mapping[str, ResourceSnapshot],
    ) -> None:
        resource = resources.get(item.resource_id)
        if resource is None and item.action is PlanAction.DELETE:
            resource = Resource(item.resource_id)
        operation = self._operation(
            resource,
            snapshots.get(item.resource_id),
            item.action,
            plan.request,
        )
        if self._config.timeout.resource_refresh is None:
            await operation
        else:
            await asyncio.wait_for(operation, self._config.timeout.resource_refresh)
