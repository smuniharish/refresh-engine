"""High-level refresh orchestration API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from time import perf_counter
from uuid import uuid4

from refresh_engine.api.protocols import (
    FingerprintStrategy,
    RefreshOperation,
    ResourceSource,
    StateStore,
)
from refresh_engine.core.models import (
    ChangeSet,
    FailurePolicy,
    RefreshConfig,
    RefreshIssue,
    RefreshMode,
    RefreshRequest,
    RefreshResult,
    RefreshStatus,
    Resource,
    ResourceSnapshot,
    ResourceState,
    TriggerSource,
    utc_now,
)
from refresh_engine.detection import ChangeDetector, CompositeHash
from refresh_engine.errors import DiscoveryError
from refresh_engine.events import EventKind, EventPublisher, RefreshEvent
from refresh_engine.execution.coordinator import RefreshCoordinator
from refresh_engine.execution.executor import RefreshExecutor
from refresh_engine.planning import DependencyGraph, ImpactAnalyzer, RefreshPlanner
from refresh_engine.stores import InMemoryStateStore


class RefreshEngine:
    """Generic change detection and refresh orchestration."""

    def __init__(
        self,
        source: ResourceSource,
        operation: RefreshOperation,
        *,
        fingerprint: FingerprintStrategy | None = None,
        store: StateStore | None = None,
        config: RefreshConfig | None = None,
        events: EventPublisher | None = None,
    ) -> None:
        self.source = source
        self.operation = operation
        self.fingerprint = fingerprint or CompositeHash()
        self.store = store or InMemoryStateStore()
        self.config = config or RefreshConfig()
        self.events = events or EventPublisher()
        self._detector = ChangeDetector()
        self._planner = RefreshPlanner()
        self._impact = ImpactAnalyzer()
        self._executor = RefreshExecutor(operation, self.config, self.events)
        self._coordinator = RefreshCoordinator(
            self._run_refresh, self.config.overlap_policy
        )

    async def refresh(
        self,
        *,
        mode: RefreshMode = RefreshMode.INCREMENTAL,
        resource_ids: frozenset[str] | set[str] | tuple[str, ...] = frozenset(),
        trigger: TriggerSource = TriggerSource.MANUAL,
        metadata: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> RefreshResult:
        request = RefreshRequest(
            mode=mode,
            resource_ids=frozenset(resource_ids),
            trigger=trigger,
            metadata=metadata or {},
            correlation_id=correlation_id,
        )
        return await self.submit(request)

    async def submit(self, request: RefreshRequest) -> RefreshResult:
        return await self._coordinator.submit(request)

    async def close(self) -> None:
        await self._coordinator.close(
            cancel_active=self.config.cancel_active_on_shutdown
        )
        await self.events.drain()

    async def _run_refresh(self, request: RefreshRequest) -> RefreshResult:
        if self.config.timeout.overall is None:
            return await self._execute_lifecycle(request)
        return await asyncio.wait_for(
            self._execute_lifecycle(request), self.config.timeout.overall
        )

    async def _execute_lifecycle(self, request: RefreshRequest) -> RefreshResult:
        refresh_id = str(uuid4())
        started_at = utc_now()
        timer = perf_counter()
        await self.events.publish(RefreshEvent(EventKind.REFRESH_STARTED, refresh_id))
        try:
            previous = await self.store.load_all()
            resources, complete, discovery_diagnostics = await self._discover(
                refresh_id
            )
            states, snapshots, preprocessing_errors = (
                await self._snapshot_and_fingerprint(
                    refresh_id, request, resources, previous
                )
            )
            await self.events.publish(
                RefreshEvent(EventKind.DETECTION_STARTED, refresh_id)
            )
            changes = self._detector.detect(
                previous,
                states,
                discovery_complete=complete,
                diagnostics=discovery_diagnostics,
            )
            await self.events.publish(
                RefreshEvent(EventKind.DETECTION_COMPLETED, refresh_id)
            )
            graph = self._build_graph(previous, states)
            impact = self._impact.analyze(changes, graph)
            await self.events.publish(
                RefreshEvent(EventKind.PLANNING_STARTED, refresh_id)
            )
            plan = self._planner.plan(
                refresh_id, request, changes, impact, graph, states
            )
            await self._load_plan_snapshots(plan, resources, snapshots)
            await self.events.publish(
                RefreshEvent(EventKind.PLANNING_COMPLETED, refresh_id)
            )
            executions = await self._executor.execute(plan, resources, snapshots)
            await self._persist(refresh_id, states, changes, executions)
            errors = preprocessing_errors + tuple(
                result.error for result in executions if result.error is not None
            )
            failed_count = len(errors)
            status = (
                RefreshStatus.SUCCESS
                if failed_count == 0
                else (
                    RefreshStatus.FAILED
                    if not executions
                    or self.config.failure_policy is FailurePolicy.FAIL_FAST
                    else RefreshStatus.PARTIAL
                )
            )
            result = self._result(
                refresh_id,
                status,
                started_at,
                timer,
                len(resources),
                changes,
                len(impact.indirectly_impacted),
                executions,
                plan.skipped_count,
                errors,
                changes.diagnostics,
            )
            await self.events.publish(
                RefreshEvent(EventKind.REFRESH_COMPLETED, refresh_id)
            )
            return result
        except asyncio.CancelledError:
            await self.events.publish(
                RefreshEvent(EventKind.REFRESH_CANCELLED, refresh_id)
            )
            raise
        except Exception as error:
            issue = RefreshIssue(
                "refresh", str(error), exception_type=type(error).__name__
            )
            await self.events.publish(
                RefreshEvent(
                    EventKind.REFRESH_FAILED,
                    refresh_id,
                    attributes={"error_type": type(error).__name__},
                )
            )
            return self._result(
                refresh_id,
                RefreshStatus.FAILED,
                started_at,
                timer,
                0,
                ChangeSet(()),
                0,
                (),
                0,
                (issue,),
                (),
            )

    async def _discover(
        self, refresh_id: str
    ) -> tuple[dict[str, Resource], bool, tuple[str, ...]]:
        await self.events.publish(RefreshEvent(EventKind.DISCOVERY_STARTED, refresh_id))

        async def consume() -> tuple[dict[str, Resource], bool, tuple[str, ...]]:
            try:
                result = await self.source.discover()
                resources: dict[str, Resource] = {}
                async for resource in result.resources:
                    if resource.resource_id in resources:
                        raise DiscoveryError(
                            f"duplicate resource id: {resource.resource_id}"
                        )
                    resources[resource.resource_id] = resource
                return resources, result.complete, result.diagnostics
            except asyncio.CancelledError:
                raise
            except DiscoveryError:
                raise
            except Exception as error:
                raise DiscoveryError("resource discovery failed") from error

        if self.config.timeout.discovery is None:
            discovered = await consume()
        else:
            discovered = await asyncio.wait_for(
                consume(), self.config.timeout.discovery
            )
        await self.events.publish(
            RefreshEvent(
                EventKind.DISCOVERY_COMPLETED,
                refresh_id,
                attributes={"count": len(discovered[0]), "complete": discovered[1]},
            )
        )
        return discovered

    async def _snapshot_and_fingerprint(
        self,
        refresh_id: str,
        request: RefreshRequest,
        resources: Mapping[str, Resource],
        previous: Mapping[str, ResourceState],
    ) -> tuple[
        dict[str, ResourceState],
        dict[str, ResourceSnapshot],
        tuple[RefreshIssue, ...],
    ]:
        states: dict[str, ResourceState] = {}
        snapshots: dict[str, ResourceSnapshot] = {}
        errors: list[RefreshIssue] = []
        queue: asyncio.Queue[Resource | None] = asyncio.Queue()
        for resource in resources.values():
            queue.put_nowait(resource)
        worker_count = min(self.config.max_concurrency, max(1, len(resources)))
        for _ in range(worker_count):
            queue.put_nowait(None)

        async def worker() -> None:
            while (resource := await queue.get()) is not None:
                old = previous.get(resource.resource_id)
                force_snapshot = request.mode is RefreshMode.FULL or (
                    request.mode is RefreshMode.TARGETED
                    and resource.resource_id in request.resource_ids
                )
                cheap_match = (
                    not force_snapshot
                    and old is not None
                    and resource.cheap_indicator_reliable
                    and resource.cheap_indicator is not None
                    and resource.cheap_indicator == old.cheap_indicator
                )
                if cheap_match:
                    states[resource.resource_id] = ResourceState(
                        resource.resource_id,
                        old.fingerprint,
                        resource.version,
                        resource.cheap_indicator,
                        old.snapshot_metadata,
                        resource.dependencies,
                        old.refreshed_at,
                    )
                    continue
                try:
                    snapshot_call = self.source.snapshot(resource)
                    if self.config.timeout.snapshot is None:
                        snapshot = await snapshot_call
                    else:
                        snapshot = await asyncio.wait_for(
                            snapshot_call, self.config.timeout.snapshot
                        )
                    fingerprint = await self.fingerprint.fingerprint(snapshot)
                    snapshots[resource.resource_id] = snapshot
                    states[resource.resource_id] = ResourceState(
                        resource.resource_id,
                        fingerprint,
                        snapshot.version or resource.version,
                        resource.cheap_indicator,
                        snapshot.metadata,
                        snapshot.dependencies or resource.dependencies,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    errors.append(
                        RefreshIssue(
                            "snapshot",
                            str(error),
                            resource.resource_id,
                            type(error).__name__,
                        )
                    )
                    if old is not None:
                        states[resource.resource_id] = old

        workers = [
            asyncio.create_task(worker(), name=f"refresh:{refresh_id}:snapshot:{index}")
            for index in range(worker_count)
        ]
        try:
            await asyncio.gather(*workers)
        except asyncio.CancelledError:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise
        return states, snapshots, tuple(errors)

    @staticmethod
    def _build_graph(
        previous: Mapping[str, ResourceState],
        current: Mapping[str, ResourceState],
    ) -> DependencyGraph:
        graph = DependencyGraph()
        effective = dict(previous)
        effective.update(current)
        for state in effective.values():
            graph.add_resource(state.resource_id)
            for dependency in state.dependencies:
                graph.add_dependency(state.resource_id, dependency)
        return graph

    async def _load_plan_snapshots(
        self,
        plan: object,
        resources: Mapping[str, Resource],
        snapshots: dict[str, ResourceSnapshot],
    ) -> None:
        from refresh_engine.core.models import PlanAction, RefreshPlan

        assert isinstance(plan, RefreshPlan)
        missing = [
            resources[item.resource_id]
            for item in plan.items
            if item.action is PlanAction.REFRESH
            and item.resource_id in resources
            and item.resource_id not in snapshots
        ]
        queue: asyncio.Queue[Resource | None] = asyncio.Queue()
        for resource in missing:
            queue.put_nowait(resource)
        worker_count = min(self.config.max_concurrency, len(missing))
        for _ in range(worker_count):
            queue.put_nowait(None)

        async def worker() -> None:
            while (resource := await queue.get()) is not None:
                snapshot_call = self.source.snapshot(resource)
                if self.config.timeout.snapshot is None:
                    snapshots[resource.resource_id] = await snapshot_call
                else:
                    snapshots[resource.resource_id] = await asyncio.wait_for(
                        snapshot_call, self.config.timeout.snapshot
                    )

        workers = [
            asyncio.create_task(
                worker(), name=f"refresh:{plan.refresh_id}:plan-snapshot:{index}"
            )
            for index in range(worker_count)
        ]
        if workers:
            try:
                await asyncio.gather(*workers)
            except asyncio.CancelledError:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise

    async def _persist(
        self,
        refresh_id: str,
        states: Mapping[str, ResourceState],
        changes: ChangeSet,
        executions: tuple[object, ...],
    ) -> None:
        from refresh_engine.core.models import ResourceExecutionResult

        typed = tuple(
            result
            for result in executions
            if isinstance(result, ResourceExecutionResult)
        )
        if self.config.failure_policy is FailurePolicy.FAIL_FAST and any(
            not result.succeeded for result in typed
        ):
            return
        async with self.store.transaction() as transaction:
            for change in changes.unchanged:
                if change.current is not None:
                    await transaction.put(change.current)
            for result in typed:
                if not result.succeeded:
                    continue
                if result.action.value == "delete":
                    await transaction.delete(result.resource_id)
                elif result.resource_id in states:
                    await transaction.put(states[result.resource_id])
        await self.events.publish(RefreshEvent(EventKind.STATE_PERSISTED, refresh_id))

    @staticmethod
    def _result(
        refresh_id: str,
        status: RefreshStatus,
        started_at: object,
        timer: float,
        discovered_count: int,
        changes: ChangeSet,
        impacted_count: int,
        executions: tuple[object, ...],
        skipped_count: int,
        errors: tuple[RefreshIssue, ...],
        warnings: tuple[str, ...],
    ) -> RefreshResult:
        from datetime import datetime

        from refresh_engine.core.models import ResourceExecutionResult

        assert isinstance(started_at, datetime)
        typed = tuple(
            result
            for result in executions
            if isinstance(result, ResourceExecutionResult)
        )
        completed = utc_now()
        return RefreshResult(
            refresh_id=refresh_id,
            status=status,
            started_at=started_at,
            completed_at=completed,
            duration=perf_counter() - timer,
            discovered_count=discovered_count,
            added_count=len(changes.added),
            modified_count=len(changes.modified),
            deleted_count=len(changes.deleted),
            unchanged_count=len(changes.unchanged),
            impacted_count=impacted_count,
            refreshed_count=sum(result.succeeded for result in typed),
            skipped_count=skipped_count,
            failed_count=len(errors),
            errors=errors,
            warnings=warnings,
            executions=typed,
        )
