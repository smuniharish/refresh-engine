"""Immutable public models used by refresh-engine."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum, auto
from types import MappingProxyType
from typing import Any
from uuid import uuid4

ResourceId = str
Metadata = Mapping[str, Any]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def freeze_mapping(value: Mapping[str, Any] | None = None) -> Metadata:
    """Return a shallow immutable copy of metadata."""
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True, slots=True)
class Resource:
    """Stable resource identity and lightweight discovery metadata."""

    resource_id: ResourceId
    metadata: Metadata = field(default_factory=freeze_mapping)
    version: str | None = None
    dependencies: frozenset[ResourceId] = frozenset()
    source: str | None = None
    cheap_indicator: str | None = None
    cheap_indicator_reliable: bool = False

    def __post_init__(self) -> None:
        if not self.resource_id:
            raise ValueError("resource_id must not be empty")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        object.__setattr__(self, "dependencies", frozenset(self.dependencies))


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Observable resource state used to create a strong fingerprint."""

    resource_id: ResourceId
    content: Any = None
    metadata: Metadata = field(default_factory=freeze_mapping)
    version: str | None = None
    version_token: str | None = None
    etag: str | None = None
    dependencies: frozenset[ResourceId] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        object.__setattr__(self, "dependencies", frozenset(self.dependencies))


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """A streaming discovery plus an explicit deletion-safety contract."""

    resources: AsyncIterator[Resource]
    complete: bool = True
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceFingerprint:
    """A namespaced deterministic fingerprint."""

    algorithm: str
    value: str


@dataclass(frozen=True, slots=True)
class ResourceState:
    """Last successfully processed resource state."""

    resource_id: ResourceId
    fingerprint: ResourceFingerprint
    version: str | None = None
    cheap_indicator: str | None = None
    snapshot_metadata: Metadata = field(default_factory=freeze_mapping)
    dependencies: frozenset[ResourceId] = frozenset()
    refreshed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "snapshot_metadata", freeze_mapping(self.snapshot_metadata)
        )
        object.__setattr__(self, "dependencies", frozenset(self.dependencies))


class ChangeType(StrEnum):
    ADDED = auto()
    MODIFIED = auto()
    DELETED = auto()
    UNCHANGED = auto()
    DEPENDENCY_CHANGED = auto()


@dataclass(frozen=True, slots=True)
class Change:
    resource_id: ResourceId
    change_type: ChangeType
    previous: ResourceState | None = None
    current: ResourceState | None = None


@dataclass(frozen=True, slots=True)
class ChangeSet:
    changes: tuple[Change, ...]
    diagnostics: tuple[str, ...] = ()

    def of_type(self, change_type: ChangeType) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.change_type is change_type)

    @property
    def added(self) -> tuple[Change, ...]:
        return self.of_type(ChangeType.ADDED)

    @property
    def modified(self) -> tuple[Change, ...]:
        return self.of_type(ChangeType.MODIFIED)

    @property
    def deleted(self) -> tuple[Change, ...]:
        return self.of_type(ChangeType.DELETED)

    @property
    def unchanged(self) -> tuple[Change, ...]:
        return self.of_type(ChangeType.UNCHANGED)

    @property
    def dependency_changed(self) -> tuple[Change, ...]:
        return self.of_type(ChangeType.DEPENDENCY_CHANGED)

    @property
    def counts(self) -> Mapping[ChangeType, int]:
        return MappingProxyType({kind: len(self.of_type(kind)) for kind in ChangeType})

    @property
    def resource_ids(self) -> frozenset[ResourceId]:
        return frozenset(change.resource_id for change in self.changes)


@dataclass(frozen=True, slots=True)
class ImpactSet:
    directly_changed: frozenset[ResourceId]
    indirectly_impacted: frozenset[ResourceId]
    unaffected: frozenset[ResourceId]

    @property
    def all_impacted(self) -> frozenset[ResourceId]:
        return self.directly_changed | self.indirectly_impacted


class RefreshMode(StrEnum):
    FULL = auto()
    INCREMENTAL = auto()
    TARGETED = auto()
    DEPENDENCY_AWARE = auto()


class TriggerSource(StrEnum):
    MANUAL = auto()
    SCHEDULED = auto()
    EVENT = auto()
    QUERY = auto()
    EXTERNAL = auto()


class OverlapPolicy(StrEnum):
    SKIP_IF_RUNNING = auto()
    QUEUE = auto()
    COALESCE = auto()
    CANCEL_PREVIOUS = auto()


class FailurePolicy(StrEnum):
    FAIL_FAST = auto()
    BEST_EFFORT = auto()
    RETRY_THEN_CONTINUE = auto()


class RefreshStatus(StrEnum):
    SUCCESS = auto()
    PARTIAL = auto()
    FAILED = auto()
    CANCELLED = auto()
    SKIPPED = auto()


@dataclass(frozen=True, slots=True)
class RefreshRequest:
    mode: RefreshMode = RefreshMode.INCREMENTAL
    resource_ids: frozenset[ResourceId] = frozenset()
    trigger: TriggerSource = TriggerSource.MANUAL
    priority: int = 0
    metadata: Metadata = field(default_factory=freeze_mapping)
    requested_at: datetime = field(default_factory=utc_now)
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_ids", frozenset(self.resource_ids))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        if self.mode is RefreshMode.TARGETED and not self.resource_ids:
            raise ValueError("targeted refresh requires resource_ids")

    @property
    def deduplication_key(self) -> tuple[RefreshMode, frozenset[str], str | None]:
        return self.mode, self.resource_ids, self.correlation_id

    def merge(self, other: RefreshRequest) -> RefreshRequest:
        mode = (
            RefreshMode.FULL
            if RefreshMode.FULL in (self.mode, other.mode)
            else (
                RefreshMode.DEPENDENCY_AWARE
                if RefreshMode.DEPENDENCY_AWARE in (self.mode, other.mode)
                else self.mode
            )
        )
        return RefreshRequest(
            mode=mode,
            resource_ids=self.resource_ids | other.resource_ids,
            trigger=other.trigger,
            priority=max(self.priority, other.priority),
            metadata={**self.metadata, **other.metadata},
            requested_at=min(self.requested_at, other.requested_at),
            correlation_id=other.correlation_id or self.correlation_id,
        )


class PlanAction(StrEnum):
    REFRESH = auto()
    DELETE = auto()


@dataclass(frozen=True, slots=True)
class PlanItem:
    resource_id: ResourceId
    action: PlanAction
    state: ResourceState | None
    dependencies: frozenset[ResourceId] = frozenset()


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    refresh_id: str
    request: RefreshRequest
    levels: tuple[tuple[PlanItem, ...], ...]
    skipped_count: int

    @property
    def items(self) -> tuple[PlanItem, ...]:
        return tuple(item for level in self.levels for item in level)


@dataclass(frozen=True, slots=True)
class RefreshIssue:
    phase: str
    message: str
    resource_id: ResourceId | None = None
    exception_type: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceExecutionResult:
    resource_id: ResourceId
    action: PlanAction
    succeeded: bool
    attempts: int
    duration: float
    error: RefreshIssue | None = None


@dataclass(frozen=True, slots=True)
class RefreshResult:
    refresh_id: str
    status: RefreshStatus
    started_at: datetime
    completed_at: datetime
    duration: float
    discovered_count: int = 0
    added_count: int = 0
    modified_count: int = 0
    deleted_count: int = 0
    unchanged_count: int = 0
    impacted_count: int = 0
    refreshed_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    errors: tuple[RefreshIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    executions: tuple[ResourceExecutionResult, ...] = ()

    @classmethod
    def skipped(cls, reason: str) -> RefreshResult:
        now = utc_now()
        return cls(
            refresh_id=str(uuid4()),
            status=RefreshStatus.SKIPPED,
            started_at=now,
            completed_at=now,
            duration=0.0,
            skipped_count=1,
            warnings=(reason,),
        )


@dataclass(frozen=True, slots=True)
class RetryConfig:
    max_attempts: int = 1
    initial_delay: float = 0.1
    max_delay: float = 5.0
    exponential_base: float = 2.0
    jitter: float = 0.0
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if min(self.initial_delay, self.max_delay, self.jitter) < 0:
            raise ValueError("retry delays must not be negative")
        if self.exponential_base < 1:
            raise ValueError("exponential_base must be at least 1")


@dataclass(frozen=True, slots=True)
class TimeoutConfig:
    discovery: float | None = None
    snapshot: float | None = None
    resource_refresh: float | None = None
    overall: float | None = None


@dataclass(frozen=True, slots=True)
class RefreshConfig:
    max_concurrency: int = 10
    overlap_policy: OverlapPolicy = OverlapPolicy.COALESCE
    failure_policy: FailurePolicy = FailurePolicy.BEST_EFFORT
    retry: RetryConfig = field(default_factory=RetryConfig)
    timeout: TimeoutConfig = field(default_factory=TimeoutConfig)
    cancel_active_on_shutdown: bool = False

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        for value in (
            self.timeout.discovery,
            self.timeout.snapshot,
            self.timeout.resource_refresh,
            self.timeout.overall,
        ):
            if value is not None and value <= 0:
                raise ValueError("timeouts must be positive")
