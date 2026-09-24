"""Event and query adapters that normalize refresh requests."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from refresh_engine.core.models import (
    RefreshMode,
    RefreshRequest,
    TriggerSource,
)


class EventTrigger:
    def __init__(
        self,
        mapper: Callable[[object], RefreshRequest | None],
    ) -> None:
        self._mapper = mapper

    def request_for(self, event: object) -> RefreshRequest | None:
        return self._mapper(event)


class QueryTrigger:
    def request(
        self,
        resource_ids: Iterable[str],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> RefreshRequest:
        return RefreshRequest(
            mode=RefreshMode.TARGETED,
            resource_ids=frozenset(resource_ids),
            trigger=TriggerSource.QUERY,
            metadata=metadata or {},
        )
