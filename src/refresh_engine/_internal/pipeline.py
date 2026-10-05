"""The lifecycle of one refresh."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from time import perf_counter
from typing import Final

from refresh_engine._internal.concurrency import run_bounded
from refresh_engine._internal.context import (
    TERMINAL_EVENTS,
    ChangeType,
    Emitter,
    RunContext,
    RunTicket,
    describe,
    terminal_attributes,
    unrun_result,
)
from refresh_engine._internal.execution import Executor
from refresh_engine._internal.observation import (
    ObservationError,
    Observer,
    cheap_match,
    discover,
)
from refresh_engine._internal.planning import build_plan
from refresh_engine.contracts import (
    FingerprintStrategy,
    RefreshOperation,
    ResourceSource,
    StateStore,
)
from refresh_engine.events import EventKind, EventPublisherABC
from refresh_engine.models import (
    RefreshConfig,
    RefreshMode,
    RefreshPhase,
    RefreshResult,
    RefreshStatus,
    Resource,
    ResourceState,
)

_YIELD_EVERY: Final = 1024
_PREVIEW: Final = 5


class _StageAbortedError(Exception):
    """Skip the remaining stages after a recorded refresh-level failure."""


def _preview(resource_ids: Sequence[str]) -> str:
    shown = ", ".join(repr(resource_id) for resource_id in resource_ids[:_PREVIEW])
    hidden = len(resource_ids) - _PREVIEW
    return f"{shown} and {hidden} more" if hidden > 0 else shown


class RefreshPipeline:
    """Run refreshes for one engine."""

    def __init__(
        self,
        source: ResourceSource,
        operation: RefreshOperation,
        fingerprint: FingerprintStrategy,
        store: StateStore,
        config: RefreshConfig,
        events: EventPublisherABC,
    ) -> None:
        self._source = source
        self._operation = operation
        self._store = store
        self._config = config
        self._events = events
        self._observer = Observer(source, fingerprint, config.timeout.snapshot)

    async def run(self, ticket: RunTicket) -> RefreshResult:
        """Run one refresh and return its result.

        Cancellation propagates; the cancelled result is stored on the ticket.
        """
        context = RunContext(
            ticket, self._config.failure_policy, Emitter(self._events, ticket)
        )
        request = ticket.request
        started: dict[str, object] = {
            "mode": request.mode.value,
            "trigger": request.trigger.value,
        }
        if request.metadata:
            started["metadata"] = dict(request.metadata)
        context.emit(EventKind.REFRESH_STARTED, started)
        overall = self._config.timeout.overall
        try:
            scope = asyncio.timeout(overall)
            try:
                async with scope:
                    await self._stages(context)
            except TimeoutError:
                if overall is None or not scope.expired():
                    raise
                context.fail_refresh(
                    context.phase,
                    f"the refresh exceeded the overall timeout of {overall:g} seconds",
                    TimeoutError.__name__,
                )
            except _StageAbortedError:
                pass
            await self._persist(context)
        except asyncio.CancelledError as cancellation:
            context.warnings.append(str(cancellation) or "the refresh was cancelled")
            result = context.result(RefreshStatus.CANCELLED)
            ticket.cancelled_result = result
            self._finish(context.emit, result, None)
            raise
        except Exception as error:
            context.fail_refresh(
                context.phase, describe(error), type(error).__name__, error
            )
        result = context.result(context.status())
        self._finish(context.emit, result, context.refresh_error)
        return result

    def finish_unrun(
        self, ticket: RunTicket, status: RefreshStatus, reason: str
    ) -> RefreshResult:
        """Return and announce the result of a request that never ran."""
        result = unrun_result(ticket, status, reason)
        self._finish(Emitter(self._events, ticket), result, None)
        return result

    @staticmethod
    def _finish(
        emit: Emitter, result: RefreshResult, error: BaseException | None
    ) -> None:
        emit(TERMINAL_EVENTS[result.status], terminal_attributes(result), error=error)

    async def _stages(self, context: RunContext) -> None:
        await self._load(context)
        complete = await self._discover(context)
        if context.request.mode is RefreshMode.TARGETED:
            context.phase = RefreshPhase.PLANNING
            context.scope = self._targets(context)
        else:
            context.scope = list(context.discovered.values())
        await self._observe(context)
        if context.should_stop():
            raise _StageAbortedError
        context.deleted = self._deletions(context, complete=complete)
        counts = dict.fromkeys(ChangeType, 0)
        for observation in context.observations.values():
            counts[observation.change] += 1
        context.emit(
            EventKind.DETECTION_COMPLETED,
            {
                "added": counts[ChangeType.ADDED],
                "modified": counts[ChangeType.MODIFIED],
                "dependency_changed": counts[ChangeType.DEPENDENCY_CHANGED],
                "unchanged": counts[ChangeType.UNCHANGED],
                "deleted": len(context.deleted),
            },
        )
        context.phase = RefreshPhase.PLANNING
        plan = build_plan(
            context.request.mode,
            context.observations,
            context.deleted,
            context.previous,
        )
        context.planned = True
        context.planned_refreshes = frozenset(plan.refresh_ids)
        context.impacted = plan.impacted
        context.emit(
            EventKind.PLANNING_COMPLETED,
            {
                "refresh": len(context.planned_refreshes),
                "delete": len(context.deleted),
                "impacted": plan.impacted,
            },
        )
        context.phase = RefreshPhase.EXECUTION
        await Executor(
            context, plan, self._operation, self._config, self._observer
        ).run()

    async def _load(self, context: RunContext) -> None:
        context.phase = RefreshPhase.PERSISTENCE
        try:
            context.previous = await self._store.load_all()
        except Exception as error:
            context.fail_refresh(
                RefreshPhase.PERSISTENCE,
                f"failed to load state: {describe(error)}",
                type(error).__name__,
                error,
            )
            raise _StageAbortedError from error

    async def _discover(self, context: RunContext) -> bool:
        context.phase = RefreshPhase.DISCOVERY
        context.emit(EventKind.DISCOVERY_STARTED)
        started = perf_counter()
        try:
            discovered, complete, diagnostics = await discover(
                self._source, self._config.timeout.discovery
            )
        except Exception as error:
            context.fail_refresh(
                RefreshPhase.DISCOVERY,
                f"discovery failed: {describe(error)}",
                type(error).__name__,
                error,
            )
            raise _StageAbortedError from error
        context.discovered = discovered
        context.warnings.extend(diagnostics)
        context.emit(
            EventKind.DISCOVERY_COMPLETED,
            {
                "discovered": len(discovered),
                "complete": complete,
                "duration": perf_counter() - started,
            },
        )
        return complete

    @staticmethod
    def _targets(context: RunContext) -> list[Resource]:
        request = context.request
        discovered = context.discovered
        selector = request.selector
        if selector is None:
            return [
                discovered[resource_id]
                for resource_id in sorted(request.resource_ids)
                if resource_id in discovered
            ]
        ids = request.resource_ids
        targets: list[Resource] = []
        matched = False
        try:
            for resource_id, resource in discovered.items():
                if selector.matches(resource):
                    matched = True
                    targets.append(resource)
                elif resource_id in ids:
                    targets.append(resource)
        except Exception as error:
            context.fail_refresh(
                RefreshPhase.PLANNING,
                f"the selector failed: {describe(error)}",
                type(error).__name__,
                error,
            )
            raise _StageAbortedError from error
        if not matched:
            context.warnings.append("the selector matched no discovered resources")
        return targets

    async def _observe(self, context: RunContext) -> None:
        context.phase = RefreshPhase.SNAPSHOT
        scope = context.scope
        retain_all = context.request.mode in (RefreshMode.FULL, RefreshMode.TARGETED)
        context.emit(EventKind.SNAPSHOT_STARTED, {"resources": len(scope)})
        started = perf_counter()
        previous = context.previous
        observations = context.observations
        pending: list[tuple[Resource, ResourceState | None]] = []
        for position, resource in enumerate(scope, 1):
            state = previous.get(resource.resource_id)
            observation = None if retain_all else cheap_match(resource, state)
            if observation is None:
                pending.append((resource, state))
            else:
                observations[resource.resource_id] = observation
            if position % _YIELD_EVERY == 0:
                await asyncio.sleep(0)
        context.cheap_matches = len(observations)

        async def observe(item: tuple[Resource, ResourceState | None]) -> None:
            resource, state = item
            resource_id = resource.resource_id
            try:
                snapshot, observation = await self._observer.observe(resource, state)
            except ObservationError as failure:
                context.observation_failures += 1
                issue = context.fail_resource(
                    resource_id,
                    failure.phase,
                    describe(failure.error),
                    type(failure.error).__name__,
                )
                context.emit(
                    EventKind.RESOURCE_SNAPSHOT_FAILED,
                    {"phase": failure.phase.value, "error_type": issue.exception_type},
                    resource_id=resource_id,
                    error=failure.error,
                )
                return
            observations[resource_id] = observation
            if retain_all or observation.change is not ChangeType.UNCHANGED:
                context.snapshots[resource_id] = snapshot

        await run_bounded(
            pending, observe, self._config.max_concurrency, context.should_stop
        )
        context.emit(
            EventKind.SNAPSHOT_COMPLETED,
            {
                "snapshotted": len(observations) - context.cheap_matches,
                "cheap_matches": context.cheap_matches,
                "failed": context.observation_failures,
                "duration": perf_counter() - started,
            },
        )

    @staticmethod
    def _deletions(context: RunContext, *, complete: bool) -> list[str]:
        request = context.request
        previous = context.previous
        discovered = context.discovered
        if request.mode is RefreshMode.TARGETED:
            missing = sorted(
                resource_id
                for resource_id in request.resource_ids
                if resource_id not in discovered
            )
            unknown = [
                resource_id for resource_id in missing if resource_id not in previous
            ]
            if unknown:
                context.warnings.append(
                    f"targeted resources were not discovered: {_preview(unknown)}"
                )
            candidates = [
                resource_id for resource_id in missing if resource_id in previous
            ]
        else:
            candidates = sorted(previous.keys() - discovered.keys())
        if complete or not candidates:
            return candidates
        context.warnings.append(
            f"deletion of {len(candidates)} undiscovered resources was suppressed "
            "because discovery was incomplete"
        )
        return []

    async def _persist(self, context: RunContext) -> None:
        if context.should_stop():
            return
        puts: list[ResourceState] = []
        refreshed_at = context.refreshed_at
        planned = context.planned_refreshes
        refresh_id = context.ticket.refresh_id
        for resource_id, observation in context.observations.items():
            completed = refreshed_at.get(resource_id)
            previous = observation.previous
            if completed is not None:
                puts.append(observation.state(completed))
            elif previous is None or observation.change is not ChangeType.UNCHANGED:
                continue
            elif resource_id in planned:
                puts.append(observation.retry_state(refresh_id, previous))
            else:
                stale = observation.stale_state(previous)
                if stale is not None:
                    puts.append(stale)
        deletes = context.deletions_applied
        if not puts and not deletes:
            return
        context.phase = RefreshPhase.PERSISTENCE
        started = perf_counter()
        transaction = self._store.transaction()
        try:
            for state in puts:
                await transaction.put(state)
            for resource_id in deletes:
                await transaction.delete(resource_id)
            await transaction.commit()
        except BaseException as error:
            with suppress(Exception):
                await transaction.rollback()
            if not isinstance(error, Exception):
                raise
            context.fail_refresh(
                RefreshPhase.PERSISTENCE,
                f"failed to commit state: {describe(error)}",
                type(error).__name__,
                error,
            )
            return
        context.emit(
            EventKind.STATE_PERSISTED,
            {
                "written": len(puts),
                "deleted": len(deletes),
                "duration": perf_counter() - started,
            },
        )
