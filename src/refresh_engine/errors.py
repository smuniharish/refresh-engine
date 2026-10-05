"""Exceptions raised or reported by refresh-engine.

Every exception derives from [`RefreshError`][refresh_engine.RefreshError]. Failures
that happen while a refresh runs are not raised to the caller; the engine records
them as [`RefreshIssue`][refresh_engine.RefreshIssue] values whose
`exception_type` names the exception class.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "ConfigurationError",
    "DependencyCycleError",
    "DiscoveryError",
    "EngineClosedError",
    "FingerprintError",
    "RefreshError",
    "SchedulerError",
    "SnapshotError",
    "StateStoreError",
]


class RefreshError(Exception):
    """Base class for every refresh-engine exception."""


class ConfigurationError(RefreshError, ValueError):
    """An argument, model field, or configuration value is invalid.

    It also derives from `ValueError`, so existing `except ValueError` handlers
    continue to work.
    """


class DiscoveryError(RefreshError):
    """Resource discovery failed or produced invalid data.

    Sources may raise it to signal that a discovery cannot be trusted. The engine
    reports discovery failures in `RefreshResult.errors` and never interprets a
    failed discovery as an empty one.
    """


class SnapshotError(RefreshError):
    """A resource snapshot could not be read or violated the source contract."""


class FingerprintError(RefreshError):
    """A snapshot value cannot be fingerprinted deterministically."""


class DependencyCycleError(RefreshError):
    """Resources planned for refresh depend on each other in a cycle.

    Attributes:
        cycle: Resource IDs along the cycle. The first and last IDs are equal.
    """

    def __init__(self, cycle: Sequence[str]) -> None:
        """Create the error for the given cycle.

        Args:
            cycle: Resource IDs along the cycle, starting and ending with the same ID.
        """
        self.cycle = tuple(cycle)
        super().__init__("dependency cycle detected: " + " -> ".join(self.cycle))

    def __reduce__(self) -> tuple[type[DependencyCycleError], tuple[tuple[str, ...]]]:
        """Support pickling by reconstructing the error from its cycle."""
        return type(self), (self.cycle,)


class StateStoreError(RefreshError):
    """A state store failed to load or commit resource state."""


class SchedulerError(RefreshError):
    """A scheduler lifecycle method was called in an invalid state."""


class EngineClosedError(RefreshError):
    """A refresh was submitted to an engine that is closing or closed."""
