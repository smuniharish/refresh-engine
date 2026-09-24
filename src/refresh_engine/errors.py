"""Structured refresh-engine exceptions."""


class RefreshError(Exception):
    """Base exception for refresh-engine failures."""


class ConfigurationError(RefreshError):
    """Invalid configuration."""


class DiscoveryError(RefreshError):
    """Resource discovery failed."""


class SnapshotError(RefreshError):
    """Resource snapshotting failed."""


class FingerprintError(RefreshError):
    """Fingerprint creation failed."""


class ChangeDetectionError(RefreshError):
    """Change detection failed."""


class DependencyError(RefreshError):
    """Dependency graph operation failed."""


class DependencyCycleError(DependencyError):
    """A dependency cycle prevents deterministic execution."""

    def __init__(self, cycle: tuple[str, ...]) -> None:
        self.cycle = cycle
        super().__init__(f"dependency cycle detected: {' -> '.join(cycle)}")


class PlanningError(RefreshError):
    """Refresh planning failed."""


class ExecutionError(RefreshError):
    """Refresh execution failed."""


class StateStoreError(RefreshError):
    """State persistence failed."""


class SchedulerError(RefreshError):
    """Scheduler lifecycle operation failed."""


class CancellationError(RefreshError):
    """A refresh was cancelled by policy."""
