"""Structured lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, auto

from refresh_engine.core.models import Metadata, freeze_mapping, utc_now


class EventKind(StrEnum):
    REFRESH_STARTED = auto()
    DISCOVERY_STARTED = auto()
    DISCOVERY_COMPLETED = auto()
    DETECTION_STARTED = auto()
    DETECTION_COMPLETED = auto()
    PLANNING_STARTED = auto()
    PLANNING_COMPLETED = auto()
    EXECUTION_STARTED = auto()
    RESOURCE_REFRESH_STARTED = auto()
    RESOURCE_REFRESH_COMPLETED = auto()
    RESOURCE_REFRESH_FAILED = auto()
    EXECUTION_COMPLETED = auto()
    STATE_PERSISTED = auto()
    REFRESH_COMPLETED = auto()
    REFRESH_FAILED = auto()
    REFRESH_CANCELLED = auto()
    SCHEDULER_STARTED = auto()
    SCHEDULER_STOPPED = auto()


@dataclass(frozen=True, slots=True)
class RefreshEvent:
    kind: EventKind
    refresh_id: str | None = None
    resource_id: str | None = None
    timestamp: datetime = field(default_factory=utc_now)
    attributes: Metadata = field(default_factory=freeze_mapping)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", freeze_mapping(self.attributes))
