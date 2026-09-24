"""Safe lifecycle event logging through structlog."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import structlog
from structlog.typing import FilteringBoundLogger

from refresh_engine.events.models import RefreshEvent

Redactor = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def default_redactor(values: Mapping[str, Any]) -> Mapping[str, Any]:
    sensitive = {"api_key", "authorization", "password", "secret", "token"}
    return {
        key: "[REDACTED]" if key.lower() in sensitive else value
        for key, value in values.items()
    }


class StructlogEventHandler:
    """Translate refresh events into native structlog events.

    The application retains ownership of global structlog processor and renderer
    configuration. This handler only binds safe lifecycle fields.
    """

    def __init__(
        self,
        logger: FilteringBoundLogger | None = None,
        redactor: Redactor = default_redactor,
    ) -> None:
        self._logger = logger or structlog.get_logger("refresh_engine")
        self._redactor = redactor

    def __call__(self, event: object) -> None:
        if not isinstance(event, RefreshEvent):
            return
        fields = {
            "refresh_id": event.refresh_id,
            "resource_id": event.resource_id,
            "event_kind": event.kind.value,
            "timestamp": event.timestamp.isoformat(),
            **event.attributes,
        }
        self._logger.info("refresh_event", **dict(self._redactor(fields)))
