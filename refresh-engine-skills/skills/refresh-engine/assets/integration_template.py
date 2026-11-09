"""Checklist-shaped integration template for refresh-engine 1.0.0.

Use the installed package's public ABC signatures to complete these classes.
The API may still be settling, so this template intentionally does not encode
abstract method signatures.
"""

from __future__ import annotations

from refresh_engine import (
    EventHandler,
    FingerprintStrategy,
    MetricsSink,
    RefreshOperation,
    ResourceSelector,
    ResourceSource,
    StateStore,
)


class ApplicationSource(ResourceSource):
    """Adapt the installed ResourceSource ABC for the application's domain."""


class ApplicationOperation(RefreshOperation):
    """Use only if an operation object is needed instead of a plain async function."""


class ApplicationFingerprint(FingerprintStrategy):
    """Adapt fingerprinting to the application's resources."""


class ApplicationStore(StateStore):
    """Adapt state persistence; await StateStore.transaction()."""


class ApplicationSelector(ResourceSelector):
    """Adapt selection to the application's resources."""


class ApplicationMetrics(MetricsSink):
    """Implement metrics writes asynchronously."""


class ApplicationEvents(EventHandler):
    """Implement event handling asynchronously."""


async def apply_change(*args: object, **kwargs: object) -> None:
    """Plain async operation functions are accepted."""
    raise NotImplementedError


async def handle_event(*args: object, **kwargs: object) -> None:
    """Plain async event-handler functions are accepted."""
    raise NotImplementedError
