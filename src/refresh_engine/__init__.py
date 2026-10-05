"""Asyncio-first, domain-neutral change detection and refresh orchestration.

Every supported name is importable from this package:

```python
from refresh_engine import RefreshEngine, Resource, ResourceSnapshot
```
"""

from refresh_engine.contracts import (
    FingerprintStrategy,
    FingerprintStrategyABC,
    MetricsSink,
    MetricsSinkABC,
    RefreshOperation,
    RefreshOperationABC,
    ResourceSelector,
    ResourceSelectorABC,
    ResourceSource,
    ResourceSourceABC,
    StateStore,
    StateStoreABC,
    StateTransaction,
    StateTransactionABC,
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
    EventHandler,
    EventKind,
    EventPublisher,
    EventPublisherABC,
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
    ExternalSchedulerAdapter,
    SchedulerState,
)
from refresh_engine.selectors import IDSelector, PredicateSelector, TagSelector
from refresh_engine.stores import InMemoryStateStore

__version__ = "0.2.0"

__all__ = [
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
    "EventKind",
    "EventPublisher",
    "EventPublisherABC",
    "ExternalSchedulerAdapter",
    "FailurePolicy",
    "FingerprintError",
    "FingerprintStrategy",
    "FingerprintStrategyABC",
    "IDSelector",
    "InMemoryMetrics",
    "InMemoryStateStore",
    "MetadataHash",
    "MetricsEventHandler",
    "MetricsSink",
    "MetricsSinkABC",
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
    "RefreshOperationABC",
    "RefreshPhase",
    "RefreshRequest",
    "RefreshResult",
    "RefreshStatus",
    "Resource",
    "ResourceExecutionResult",
    "ResourceFingerprint",
    "ResourceSelector",
    "ResourceSelectorABC",
    "ResourceSnapshot",
    "ResourceSource",
    "ResourceSourceABC",
    "ResourceState",
    "RetryConfig",
    "SchedulerError",
    "SchedulerState",
    "SnapshotError",
    "StateStore",
    "StateStoreABC",
    "StateStoreError",
    "StateTransaction",
    "StateTransactionABC",
    "StructlogEventHandler",
    "TagSelector",
    "TimeoutConfig",
    "TriggerSource",
    "VersionTokenFingerprint",
    "canonical_digest",
    "default_redactor",
]
