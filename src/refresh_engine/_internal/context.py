"""State shared by the stages of one refresh."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto
from time import perf_counter
from typing import Any, Final
from uuid import uuid4

from refresh_engine._internal.values import EMPTY_MAPPING, utc_now
from refresh_engine.events import EventKind, EventPublisherABC, RefreshEvent
from refresh_engine.models import (
    FailurePolicy,
    RefreshIssue,
    RefreshPhase,
    RefreshRequest,
    RefreshResult,
    RefreshStatus,
    Resource,
    ResourceExecutionResult,
    ResourceFingerprint,
    ResourceSnapshot,
    ResourceState,
)

TERMINAL_EVENTS: Final = {
    RefreshStatus.SUCCESS: EventKind.REFRESH_COMPLETED,
    RefreshStatus.PARTIAL: EventKind.REFRESH_COMPLETED,
    RefreshStatus.FAILED: EventKind.REFRESH_FAILED,
    RefreshStatus.CANCELLED: EventKind.REFRESH_CANCELLED,
    RefreshStatus.SKIPPED: EventKind.REFRESH_SKIPPED,
}

_LOGGER: Final = logging.getLogger("refresh_engine")

RETRY_ALGORITHM: Final = "refresh-engine:retry"


def describe(error: BaseException) -> str:
    """Return a non-empty, human-readable description of `error`."""
    return str(error) or type(error).__name__


class ChangeType(StrEnum):
    """Classification of an observed resource against its previous state."""

    ADDED = auto()
    MODIFIED = auto()
    DEPENDENCY_CHANGED = auto()
    UNCHANGED = auto()


def classify(
    previous: ResourceState | None,
    fingerprint: ResourceFingerprint,
    dependencies: frozenset[str],
) -> ChangeType:
    """Classify an observation against the previous state of the resource."""
    if previous is None:
        return ChangeType.ADDED
    if previous.fingerprint != fingerprint:
        return ChangeType.MODIFIED
    if previous.dependencies != dependencies:
        return ChangeType.DEPENDENCY_CHANGED
    return ChangeType.UNCHANGED


@dataclass(slots=True, eq=False)
class Observation:
    """Current state of a discovered resource, before it is persisted."""

    resource: Resource
    fingerprint: ResourceFingerprint
    version: str | None
    metadata: Mapping[str, Any]
    dependencies: frozenset[str]
    previous: ResourceState | None
    change: ChangeType

    def state(self, refreshed_at: datetime) -> ResourceState:
        """Return the state to persist for this observation."""
        return ResourceState(
            self.resource.resource_id,
            self.fingerprint,
            version=self.version,
            cheap_indicator=self.resource.cheap_indicator,
            snapshot_metadata=self.metadata,
            dependencies=self.dependencies,
            refreshed_at=refreshed_at,
        )

    def stale_state(self, previous: ResourceState) -> ResourceState | None:
        """Return refreshed stored state for an unchanged resource, if it drifted.

        Unchanged resources keep their fingerprint and refresh time, but their
        version, cheap indicator, and metadata are kept current.
        """
        if (
            previous.version == self.version
            and previous.cheap_indicator == self.resource.cheap_indicator
            and previous.snapshot_metadata == self.metadata
        ):
            return None
        return self.state(previous.refreshed_at)

    def retry_state(self, refresh_id: str, previous: ResourceState) -> ResourceState:
        """Return a stored state that makes the next refresh retry this resource.

        Used when a planned refresh of an unchanged resource did not succeed. The
        marker fingerprint never matches a real one, and the cheap indicator is
        cleared so the resource is read again.
        """
        return ResourceState(
            self.resource.resource_id,
            ResourceFingerprint(RETRY_ALGORITHM, refresh_id),
            version=self.version,
            snapshot_metadata=self.metadata,
            dependencies=self.dependencies,
            refreshed_at=previous.refreshed_at,
        )


@dataclass(slots=True, eq=False)
class RunTicket:
    """A request accepted by the coordinator, with its refresh ID."""

    request: RefreshRequest
    refresh_id: str = field(default_factory=lambda: str(uuid4()))
    cancelled_result: RefreshResult | None = None


class Emitter:
    """Publish events of one refresh without building them for nobody."""

    __slots__ = ("_correlation_id", "_publisher", "_refresh_id")

    def __init__(self, publisher: EventPublisherABC, ticket: RunTicket) -> None:
        self._publisher = publisher
        self._refresh_id = ticket.refresh_id
        self._correlation_id = ticket.request.correlation_id

    @property
    def enabled(self) -> bool:
        """Whether a published event can reach a handler."""
        return self._publisher.has_subscribers

    def __call__(
        self,
        kind: EventKind,
        attributes: Mapping[str, Any] = EMPTY_MAPPING,
        *,
        resource_id: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Publish one event if anybody is listening; never raise."""
        publisher = self._publisher
        if not publisher.has_subscribers:
            return
        try:
            publisher.publish(
                RefreshEvent(
                    kind,
                    refresh_id=self._refresh_id,
                    resource_id=resource_id,
                    correlation_id=self._correlation_id,
                    attributes=attributes,
                    error=error,
                )
            )
        except Exception:
            _LOGGER.exception("failed to publish %s event", kind.value)


@dataclass(slots=True, eq=False)
class RunContext:
    """Mutable state of one refresh, accumulated by the pipeline stages."""

    ticket: RunTicket
    failure_policy: FailurePolicy
    emit: Emitter
    started_at: datetime = field(default_factory=utc_now)
    timer: float = field(default_factory=perf_counter)
    phase: RefreshPhase = RefreshPhase.PERSISTENCE
    previous: Mapping[str, ResourceState] = EMPTY_MAPPING
    discovered: dict[str, Resource] = field(default_factory=dict)
    scope: list[Resource] = field(default_factory=list)
    observations: dict[str, Observation] = field(default_factory=dict)
    snapshots: dict[str, ResourceSnapshot] = field(default_factory=dict)
    cheap_matches: int = 0
    observation_failures: int = 0
    deleted: list[str] = field(default_factory=list)
    planned: bool = False
    planned_refreshes: frozenset[str] = frozenset()
    impacted: int = 0
    failures: dict[str, RefreshIssue] = field(default_factory=dict)
    issues: list[RefreshIssue] = field(default_factory=list)
    refresh_failed: bool = False
    refresh_error: BaseException | None = None
    warnings: list[str] = field(default_factory=list)
    executions: list[ResourceExecutionResult | None] = field(default_factory=list)
    refreshed_at: dict[str, datetime] = field(default_factory=dict)
    deletions_applied: list[str] = field(default_factory=list)

    @property
    def request(self) -> RefreshRequest:
        """The request being run."""
        return self.ticket.request

    def should_stop(self) -> bool:
        """Whether a fail-fast policy requires stopping new work."""
        return self.failure_policy is FailurePolicy.FAIL_FAST and bool(self.issues)

    def fail_resource(
        self,
        resource_id: str,
        phase: RefreshPhase,
        message: str,
        exception_type: str | None,
    ) -> RefreshIssue:
        """Record the failure of one resource."""
        issue = RefreshIssue(
            phase=phase,
            message=message,
            resource_id=resource_id,
            exception_type=exception_type,
        )
        self.failures[resource_id] = issue
        self.issues.append(issue)
        return issue

    def fail_refresh(
        self,
        phase: RefreshPhase,
        message: str,
        exception_type: str | None,
        error: BaseException | None = None,
    ) -> RefreshIssue:
        """Record a failure that prevents the refresh from completing."""
        issue = RefreshIssue(
            phase=phase, message=message, exception_type=exception_type
        )
        self.issues.append(issue)
        self.refresh_failed = True
        if error is not None and self.refresh_error is None:
            self.refresh_error = error
        return issue

    def status(self) -> RefreshStatus:
        """Derive the overall status from the recorded failures."""
        if not self.issues:
            return RefreshStatus.SUCCESS
        if self.refresh_failed or self.failure_policy is FailurePolicy.FAIL_FAST:
            return RefreshStatus.FAILED
        in_scope = len(self.scope) + len(self.deleted)
        if len(self.failures) < in_scope:
            return RefreshStatus.PARTIAL
        return RefreshStatus.FAILED

    def result(self, status: RefreshStatus) -> RefreshResult:
        """Build the result of the refresh."""
        counts = dict.fromkeys(ChangeType, 0)
        for observation in self.observations.values():
            counts[observation.change] += 1
        skipped = (
            len(self.discovered)
            - len(self.planned_refreshes)
            - self.observation_failures
            if self.planned
            else 0
        )
        completed_at = utc_now()
        return RefreshResult(
            refresh_id=self.ticket.refresh_id,
            request=self.request,
            status=status,
            started_at=self.started_at,
            completed_at=completed_at,
            duration=perf_counter() - self.timer,
            discovered_count=len(self.discovered),
            added_count=counts[ChangeType.ADDED],
            modified_count=counts[ChangeType.MODIFIED],
            dependency_changed_count=counts[ChangeType.DEPENDENCY_CHANGED],
            unchanged_count=counts[ChangeType.UNCHANGED],
            deleted_count=len(self.deleted),
            impacted_count=self.impacted,
            refreshed_count=len(self.refreshed_at) + len(self.deletions_applied),
            skipped_count=skipped,
            errors=tuple(self.issues),
            warnings=tuple(self.warnings),
            executions=tuple(
                execution for execution in self.executions if execution is not None
            ),
        )


def terminal_attributes(result: RefreshResult) -> dict[str, Any]:
    """Attributes of the terminal event of a refresh."""
    request = result.request
    return {
        "status": result.status.value,
        "mode": request.mode.value,
        "trigger": request.trigger.value,
        "duration": result.duration,
        "discovered": result.discovered_count,
        "added": result.added_count,
        "modified": result.modified_count,
        "dependency_changed": result.dependency_changed_count,
        "unchanged": result.unchanged_count,
        "deleted": result.deleted_count,
        "impacted": result.impacted_count,
        "refreshed": result.refreshed_count,
        "skipped": result.skipped_count,
        "failed": result.failed_count,
    }


def unrun_result(
    ticket: RunTicket, status: RefreshStatus, reason: str
) -> RefreshResult:
    """Build the result of a request that never ran."""
    now = utc_now()
    return RefreshResult(
        refresh_id=ticket.refresh_id,
        request=ticket.request,
        status=status,
        started_at=now,
        completed_at=now,
        duration=0.0,
        warnings=(reason,),
    )
