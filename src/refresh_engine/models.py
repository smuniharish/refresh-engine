"""Immutable models, enumerations, and configuration objects.

All models are frozen, slotted dataclasses. Collections passed to a model are
copied into immutable containers, so a model never changes after construction.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import KW_ONLY, dataclass, field
from datetime import datetime
from enum import StrEnum, auto
from typing import Any

from refresh_engine._internal.values import (
    freeze_mapping,
    id_set,
    number,
    optional_text,
    optional_timeout,
    positive_int,
    require_id,
    text_tuple,
    utc_now,
)
from refresh_engine.contracts import ResourceSelector
from refresh_engine.errors import ConfigurationError

__all__ = [
    "DiscoveryResult",
    "FailurePolicy",
    "OverlapPolicy",
    "PlanAction",
    "RefreshConfig",
    "RefreshIssue",
    "RefreshMode",
    "RefreshPhase",
    "RefreshRequest",
    "RefreshResult",
    "RefreshStatus",
    "Resource",
    "ResourceExecutionResult",
    "ResourceFingerprint",
    "ResourceSnapshot",
    "ResourceState",
    "RetryConfig",
    "TimeoutConfig",
    "TriggerSource",
]


def _enum[E: StrEnum](enum: type[E], value: object, name: str) -> E:
    try:
        return enum(value)
    except ValueError:
        choices = ", ".join(member.value for member in enum)
        raise ConfigurationError(f"{name} must be one of: {choices}") from None


def _optional_ids(
    value: str | Iterable[str] | None, name: str
) -> frozenset[str] | None:
    if value is None:
        return None
    return id_set(value, name)


class RefreshMode(StrEnum):
    """Which resources a refresh selects for an operation."""

    INCREMENTAL = auto()
    """Added, modified, and dependency-changed resources, plus safe deletions."""

    FULL = auto()
    """Every discovered resource regardless of change, plus safe deletions."""

    TARGETED = auto()
    """Only resources chosen by `resource_ids` or `selector`, regardless of change."""

    DEPENDENCY_AWARE = auto()
    """Incremental changes plus every discovered resource that transitively depends
    on a change."""


class TriggerSource(StrEnum):
    """What initiated a refresh request; used for observability only."""

    MANUAL = auto()
    """An explicit call from application code."""

    SCHEDULED = auto()
    """An interval scheduler."""

    EVENT = auto()
    """An application or infrastructure event."""

    QUERY = auto()
    """A user or API query for specific resources."""

    EXTERNAL = auto()
    """A scheduler or system outside the application's event loop."""


class OverlapPolicy(StrEnum):
    """How an engine handles a request while another refresh is running."""

    COALESCE = auto()
    """Merge the request into a compatible queued request, otherwise queue it."""

    QUEUE = auto()
    """Run every request separately, in submission order."""

    SKIP_IF_RUNNING = auto()
    """Return a `SKIPPED` result immediately while any refresh is running or queued."""

    CANCEL_PREVIOUS = auto()
    """Cancel the running and queued refreshes, then run the new request."""


class FailurePolicy(StrEnum):
    """How resource-level failures affect the rest of a refresh."""

    BEST_EFFORT = auto()
    """Continue after failures and persist the state of every successful resource."""

    FAIL_FAST = auto()
    """Stop starting new work after the first failure and persist no state."""


class RefreshStatus(StrEnum):
    """Overall outcome of a refresh."""

    SUCCESS = auto()
    """Every in-scope resource was observed and every planned operation succeeded."""

    PARTIAL = auto()
    """Some resources failed, but at least one in-scope resource was handled."""

    FAILED = auto()
    """The refresh could not complete, or every in-scope resource failed."""

    CANCELLED = auto()
    """The refresh was superseded or cancelled by an engine shutdown."""

    SKIPPED = auto()
    """The request was not run because another refresh was already running."""


class PlanAction(StrEnum):
    """Operation requested for a resource."""

    REFRESH = auto()
    """Apply the current snapshot of a discovered resource."""

    DELETE = auto()
    """Remove a resource that is absent from a complete discovery."""


class RefreshPhase(StrEnum):
    """Lifecycle phase in which a refresh issue occurred."""

    DISCOVERY = auto()
    """Discovering resources."""

    SNAPSHOT = auto()
    """Reading a resource snapshot."""

    FINGERPRINT = auto()
    """Fingerprinting a snapshot."""

    PLANNING = auto()
    """Selecting and ordering the resources to refresh."""

    EXECUTION = auto()
    """Running refresh or delete operations."""

    PERSISTENCE = auto()
    """Loading or committing state through the state store."""


@dataclass(frozen=True, slots=True)
class Resource:
    """Stable identity and lightweight discovery metadata of one resource.

    Attributes:
        resource_id: Stable, unique identifier of the resource.
        metadata: Application metadata available without reading the resource.
        version: Optional version label reported by discovery.
        dependencies: IDs of resources this resource depends on, or `None` when
            discovery does not report dependencies.
        cheap_indicator: Optional inexpensive change signal, such as an ETag or a
            modification token.
        cheap_indicator_reliable: Whether an unchanged `cheap_indicator` proves the
            resource is unchanged, allowing the engine to skip its snapshot.
    """

    resource_id: str
    _: KW_ONLY
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)
    version: str | None = None
    dependencies: AbstractSet[str] | None = None
    cheap_indicator: str | None = None
    cheap_indicator_reliable: bool = False

    def __post_init__(self) -> None:
        """Validate identity and freeze collections."""
        require_id(self.resource_id, "resource_id")
        optional_text(self.version, "version")
        optional_text(self.cheap_indicator, "cheap_indicator")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        object.__setattr__(
            self, "dependencies", _optional_ids(self.dependencies, "dependencies")
        )


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Observable state of a resource used for fingerprinting and refreshing.

    Attributes:
        resource_id: ID of the snapshotted resource.
        content: Resource payload passed to the refresh operation.
        metadata: Snapshot metadata, persisted with the resource state.
        version: Optional version label; overrides the discovery version.
        version_token: Optional token that changes whenever the content changes.
        etag: Optional entity tag reported by the source.
        dependencies: IDs of resources this resource depends on, or `None` to keep
            the dependencies reported by discovery.
    """

    resource_id: str
    _: KW_ONLY
    content: Any = field(default=None, hash=False)
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)
    version: str | None = None
    version_token: str | None = None
    etag: str | None = None
    dependencies: AbstractSet[str] | None = None

    def __post_init__(self) -> None:
        """Validate identity and freeze collections."""
        require_id(self.resource_id, "resource_id")
        optional_text(self.version, "version")
        optional_text(self.version_token, "version_token")
        optional_text(self.etag, "etag")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        object.__setattr__(
            self, "dependencies", _optional_ids(self.dependencies, "dependencies")
        )


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Resources produced by one discovery and whether the listing is complete.

    Attributes:
        resources: Discovered resources, as a synchronous or asynchronous iterable.
            Asynchronous iterables are consumed as a stream.
        complete: Whether the discovery is known to contain every existing resource.
            Only a complete discovery allows the engine to infer deletions.
        diagnostics: Human-readable notes reported as refresh warnings.
    """

    resources: AsyncIterable[Resource] | Iterable[Resource]
    _: KW_ONLY
    complete: bool = True
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Normalize diagnostics."""
        object.__setattr__(
            self, "diagnostics", text_tuple(self.diagnostics, "diagnostics")
        )


@dataclass(frozen=True, slots=True)
class ResourceFingerprint:
    """Deterministic digest of a snapshot, namespaced by the algorithm that made it.

    Attributes:
        algorithm: Name of the fingerprint algorithm, for example
            `sha256:composite`.
        value: Digest produced by the algorithm.
    """

    algorithm: str
    value: str

    def __post_init__(self) -> None:
        """Validate both fields."""
        require_id(self.algorithm, "algorithm")
        require_id(self.value, "value")


@dataclass(frozen=True, slots=True)
class ResourceState:
    """Last successfully refreshed state of a resource, as kept by a state store.

    Attributes:
        resource_id: ID of the resource.
        fingerprint: Fingerprint of the snapshot that was last refreshed.
        version: Version label of that snapshot.
        cheap_indicator: Cheap change indicator reported alongside that snapshot.
        snapshot_metadata: Metadata of that snapshot.
        dependencies: IDs of resources this resource depended on.
        refreshed_at: When the resource was last refreshed successfully. Defaults to the
            current UTC time.
    """

    resource_id: str
    fingerprint: ResourceFingerprint
    _: KW_ONLY
    version: str | None = None
    cheap_indicator: str | None = None
    snapshot_metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)
    dependencies: AbstractSet[str] = frozenset()
    refreshed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        """Validate identity and freeze collections."""
        require_id(self.resource_id, "resource_id")
        if not isinstance(self.fingerprint, ResourceFingerprint):
            raise ConfigurationError("fingerprint must be a ResourceFingerprint")
        object.__setattr__(
            self, "snapshot_metadata", freeze_mapping(self.snapshot_metadata)
        )
        object.__setattr__(
            self, "dependencies", id_set(self.dependencies, "dependencies")
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RefreshRequest:
    """A normalized request to refresh resources.

    `TARGETED` requests select resources with `resource_ids`, `selector`, or both;
    the other modes select resources by change and accept neither.

    Attributes:
        mode: Which resources the refresh selects.
        resource_ids: IDs to refresh in `TARGETED` mode. A single string is treated
            as one ID.
        selector: Selector evaluated against discovered resources in `TARGETED`
            mode.
        trigger: What initiated the request.
        metadata: Application metadata attached to the request.
        correlation_id: Identifier propagated to every lifecycle event.
        requested_at: When the request was created. Defaults to the current UTC time.

    Raises:
        ConfigurationError: If the mode, IDs, or selector are inconsistent.
    """

    mode: RefreshMode = RefreshMode.INCREMENTAL
    resource_ids: AbstractSet[str] = frozenset()
    selector: ResourceSelector | None = None
    trigger: TriggerSource = TriggerSource.MANUAL
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)
    correlation_id: str | None = None
    requested_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        """Validate and normalize the request."""
        mode = _enum(RefreshMode, self.mode, "mode")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(
            self, "trigger", _enum(TriggerSource, self.trigger, "trigger")
        )
        object.__setattr__(
            self, "resource_ids", id_set(self.resource_ids, "resource_ids")
        )
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))
        optional_text(self.correlation_id, "correlation_id")
        if self.selector is not None and not callable(
            getattr(self.selector, "matches", None)
        ):
            raise ConfigurationError("selector must provide a matches() method")
        selects = bool(self.resource_ids) or self.selector is not None
        if mode is RefreshMode.TARGETED and not selects:
            raise ConfigurationError(
                "a targeted refresh requires resource_ids or a selector"
            )
        if mode is not RefreshMode.TARGETED and selects:
            raise ConfigurationError(
                "resource_ids and selector are only valid for targeted refreshes"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class RefreshIssue:
    """A failure recorded during a refresh.

    Attributes:
        phase: Lifecycle phase in which the failure occurred.
        message: Human-readable description of the failure.
        resource_id: Affected resource, or `None` for a refresh-level failure.
        exception_type: Name of the exception class that caused the failure, if any.
    """

    phase: RefreshPhase
    message: str
    resource_id: str | None = None
    exception_type: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ResourceExecutionResult:
    """Outcome of one planned operation.

    Attributes:
        resource_id: ID of the resource.
        action: Operation that was planned.
        succeeded: Whether the operation completed successfully.
        attempts: Number of operation attempts; `0` if the operation never started.
        duration: Seconds spent on the resource, including retries.
        error: Failure details when `succeeded` is `False`.
    """

    resource_id: str
    action: PlanAction
    succeeded: bool
    attempts: int
    duration: float
    error: RefreshIssue | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RefreshResult:
    """Outcome and statistics of one refresh.

    Counts describe the resources the refresh considered. In every mode except
    `TARGETED`, `discovered_count` equals the sum of the four change counts plus the
    number of resources whose snapshot or fingerprint failed.

    Attributes:
        refresh_id: Unique identifier shared with the refresh's lifecycle events.
        request: The request that ran; a coalesced request when several were merged.
        status: Overall outcome.
        started_at: When the refresh started.
        completed_at: When the refresh finished.
        duration: Elapsed seconds.
        discovered_count: Resources yielded by discovery.
        added_count: Observed resources without previous state.
        modified_count: Observed resources whose fingerprint changed.
        dependency_changed_count: Observed resources whose dependencies changed.
        unchanged_count: Observed resources without changes.
        deleted_count: Resources with previous state that a complete discovery no
            longer reported.
        impacted_count: Unchanged resources refreshed because something they depend
            on changed (`DEPENDENCY_AWARE` mode only).
        refreshed_count: Planned operations, including deletions, that succeeded.
        skipped_count: Discovered resources that required no operation.
        errors: Failures, at most one per resource.
        warnings: Non-fatal diagnostics, such as suppressed deletion inference.
        executions: Outcome of each planned operation.
    """

    refresh_id: str
    request: RefreshRequest
    status: RefreshStatus
    started_at: datetime
    completed_at: datetime
    duration: float
    discovered_count: int = 0
    added_count: int = 0
    modified_count: int = 0
    dependency_changed_count: int = 0
    unchanged_count: int = 0
    deleted_count: int = 0
    impacted_count: int = 0
    refreshed_count: int = 0
    skipped_count: int = 0
    errors: tuple[RefreshIssue, ...] = ()
    warnings: tuple[str, ...] = ()
    executions: tuple[ResourceExecutionResult, ...] = ()

    @property
    def failed_count(self) -> int:
        """Number of recorded failures; equal to `len(errors)`."""
        return len(self.errors)


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryConfig:
    """Retry policy for refresh operations.

    Delays grow exponentially, `initial_delay * exponential_base ** (attempt - 1)`,
    plus a random jitter of up to `jitter` seconds, and never exceed `max_delay`.

    Attributes:
        max_attempts: Total attempts per operation, including the first one.
        initial_delay: Seconds to wait before the second attempt.
        max_delay: Upper bound for any single delay, in seconds.
        exponential_base: Growth factor applied to each subsequent delay.
        jitter: Maximum random seconds added to each delay.
        retryable_exceptions: Exception types that trigger a retry.

    Raises:
        ConfigurationError: If a value is out of range.
    """

    max_attempts: int = 1
    initial_delay: float = 0.1
    max_delay: float = 5.0
    exponential_base: float = 2.0
    jitter: float = 0.0
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)

    def __post_init__(self) -> None:
        """Validate the policy."""
        positive_int(self.max_attempts, "max_attempts")
        number(self.initial_delay, "initial_delay", minimum=0)
        number(self.max_delay, "max_delay", minimum=0)
        number(self.exponential_base, "exponential_base", minimum=1)
        number(self.jitter, "jitter", minimum=0)
        exceptions = self.retryable_exceptions
        if isinstance(exceptions, type):
            exceptions = (exceptions,)
        exceptions = tuple(exceptions)
        if not exceptions or not all(
            isinstance(kind, type) and issubclass(kind, Exception)
            for kind in exceptions
        ):
            raise ConfigurationError(
                "retryable_exceptions must contain Exception subclasses"
            )
        object.__setattr__(self, "retryable_exceptions", exceptions)


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeoutConfig:
    """Timeouts, in seconds, for each refresh phase; `None` disables a timeout.

    Attributes:
        discovery: Time allowed to start and fully consume a discovery.
        snapshot: Time allowed for each snapshot read.
        operation: Time allowed for each operation attempt.
        overall: Time allowed for discovery through execution of one refresh. State
            persistence runs afterwards and is not bounded by this timeout.

    Raises:
        ConfigurationError: If a timeout is not a positive finite number.
    """

    discovery: float | None = None
    snapshot: float | None = None
    operation: float | None = None
    overall: float | None = None

    def __post_init__(self) -> None:
        """Validate every timeout."""
        for name in ("discovery", "snapshot", "operation", "overall"):
            optional_timeout(getattr(self, name), f"timeout.{name}")


@dataclass(frozen=True, slots=True, kw_only=True)
class RefreshConfig:
    """Execution settings of a refresh engine.

    Attributes:
        max_concurrency: Maximum concurrent snapshot reads and concurrent
            operations.
        overlap_policy: How concurrent requests are handled.
        failure_policy: How resource failures affect the refresh.
        retry: Retry policy for operations.
        timeout: Timeouts for each phase.

    Raises:
        ConfigurationError: If a value is invalid.
    """

    max_concurrency: int = 10
    overlap_policy: OverlapPolicy = OverlapPolicy.COALESCE
    failure_policy: FailurePolicy = FailurePolicy.BEST_EFFORT
    retry: RetryConfig = field(default_factory=RetryConfig)
    timeout: TimeoutConfig = field(default_factory=TimeoutConfig)

    def __post_init__(self) -> None:
        """Validate and normalize the configuration."""
        positive_int(self.max_concurrency, "max_concurrency")
        object.__setattr__(
            self,
            "overlap_policy",
            _enum(OverlapPolicy, self.overlap_policy, "overlap_policy"),
        )
        object.__setattr__(
            self,
            "failure_policy",
            _enum(FailurePolicy, self.failure_policy, "failure_policy"),
        )
        if not isinstance(self.retry, RetryConfig):
            raise ConfigurationError("retry must be a RetryConfig")
        if not isinstance(self.timeout, TimeoutConfig):
            raise ConfigurationError("timeout must be a TimeoutConfig")
