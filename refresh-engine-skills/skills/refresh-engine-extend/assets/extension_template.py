"""Checklist-shaped custom integrations for refresh-engine 1.0.0.

Fill in abstract members using the installed package's public signatures.
"""

from __future__ import annotations

from refresh_engine import (
    EventHandler,
    EventPublisher,
    FingerprintStrategy,
    MetricsSink,
    RefreshOperation,
    ResourceSelector,
    ResourceSource,
    StateStore,
    StateTransaction,
)


class CustomSource(ResourceSource):
    """Implement the installed ResourceSource ABC for an application."""


class CustomFingerprint(FingerprintStrategy):
    """Implement the installed FingerprintStrategy ABC."""


class CustomStore(StateStore):
    """Implement the installed StateStore ABC and awaited transaction()."""


class CustomTransaction(StateTransaction):
    """Implement the installed StateTransaction ABC."""


class CustomSelector(ResourceSelector):
    """Implement the installed ResourceSelector ABC."""


class CustomMetrics(MetricsSink):
    """Implement MetricsSink writes asynchronously."""


class CustomPublisher(EventPublisher):
    """Implement the installed EventPublisher ABC."""


class CustomHandler(EventHandler):
    """Implement event handling asynchronously."""


class CustomOperation(RefreshOperation):
    """Optional object form; a plain async function is also accepted."""


async def apply_change(*args: object, **kwargs: object) -> None:
    """An operation may be a plain async function."""
    raise NotImplementedError


async def handle_event(*args: object, **kwargs: object) -> None:
    """An event handler may be a plain async function."""
    raise NotImplementedError
