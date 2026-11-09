"""Asyncio-first, domain-neutral change detection and refresh orchestration.

Every supported name is importable from this package:

```python
from refresh_engine import RefreshEngine, Resource, ResourceSnapshot
```
"""

from importlib.metadata import version

from refresh_engine.contracts import (
    FingerprintStrategy,
    MetricsSink,
    RefreshOperation,
    RefreshOperationCallable,
    RefreshOperationLike,
    ResourceSelector,
    ResourceSource,
    StateStore,
    StateTransaction,
)
from refresh_engine.engine import RefreshEngine
from refresh_engine.errors import (
    ConfigurationError,
    DependencyCycleError,
    DiscoveryError,
    EngineClosedError,
    FingerprintError,
    RefreshError,
    SchedulerError,
    SnapshotError,
    StateStoreError,
)
from refresh_engine.events import (
    AsyncEventPublisher,
    EventHandler,
    EventHandlerCallable,
    EventHandlerLike,
    EventKind,
    EventPublisher,
    RefreshEvent,
)
from refresh_engine.fingerprints import (
    CompositeHash,
    ContentHash,
    ETagFingerprint,
    MetadataHash,
    VersionTokenFingerprint,
    canonical_digest,
)
from refresh_engine.models import (
    DiscoveryResult,
    FailurePolicy,
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
from refresh_engine.observability import (
    InMemoryMetrics,
    MetricsEventHandler,
    StructlogEventHandler,
    default_redactor,
)
from refresh_engine.scheduling import (
    AsyncScheduler,
    SchedulerState,
)
from refresh_engine.selectors import IDSelector, PredicateSelector, TagSelector
from refresh_engine.stores import InMemoryStateStore

__version__ = version("refresh-engine")

__all__ = [
    "AsyncEventPublisher",
    "AsyncScheduler",
    "CompositeHash",
    "ConfigurationError",
    "ContentHash",
    "DependencyCycleError",
    "DiscoveryError",
    "DiscoveryResult",
    "ETagFingerprint",
    "EngineClosedError",
    "EventHandler",
    "EventHandlerCallable",
    "EventHandlerLike",
    "EventKind",
    "EventPublisher",
    "FailurePolicy",
    "FingerprintError",
    "FingerprintStrategy",
    "IDSelector",
    "InMemoryMetrics",
    "InMemoryStateStore",
    "MetadataHash",
    "MetricsEventHandler",
    "MetricsSink",
    "OverlapPolicy",
    "PlanAction",
    "PredicateSelector",
    "RefreshConfig",
    "RefreshEngine",
    "RefreshError",
    "RefreshEvent",
    "RefreshIssue",
    "RefreshMode",
    "RefreshOperation",
    "RefreshOperationCallable",
    "RefreshOperationLike",
    "RefreshPhase",
    "RefreshRequest",
    "RefreshResult",
    "RefreshStatus",
    "Resource",
    "ResourceExecutionResult",
    "ResourceFingerprint",
    "ResourceSelector",
    "ResourceSnapshot",
    "ResourceSource",
    "ResourceState",
    "RetryConfig",
    "SchedulerError",
    "SchedulerState",
    "SnapshotError",
    "StateStore",
    "StateStoreError",
    "StateTransaction",
    "StructlogEventHandler",
    "TagSelector",
    "TimeoutConfig",
    "TriggerSource",
    "VersionTokenFingerprint",
    "canonical_digest",
    "default_redactor",
]
