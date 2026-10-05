"""Lifecycle events, structured logging with redaction, and metrics."""

import asyncio
from collections.abc import MutableMapping
from typing import Any

import structlog
from common import DictionarySource, Recorder

from refresh_engine import (
    EventKind,
    EventPublisher,
    InMemoryMetrics,
    MetricsEventHandler,
    RefreshEngine,
    RefreshEvent,
    StructlogEventHandler,
)

VOLATILE = ("refresh_id", "occurred_at", "duration")


def drop_volatile_fields(
    logger: object, method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Remove values that change on every run so the output is reproducible."""
    for key in VOLATILE:
        event.pop(key, None)
    return event


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            drop_volatile_fields,
            structlog.processors.KeyValueRenderer(
                key_order=["level", "event"], sort_keys=True
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )
    events = EventPublisher()
    metrics = InMemoryMetrics()
    events.subscribe(StructlogEventHandler())
    events.subscribe(MetricsEventHandler(metrics))

    completed: list[RefreshEvent] = []

    async def remember_completed(event: RefreshEvent) -> None:
        if event.kind is EventKind.REFRESH_COMPLETED:
            completed.append(event)

    events.subscribe(remember_completed)

    source = DictionarySource({"alpha": 1, "beta": 2})
    async with RefreshEngine(source, Recorder(), events=events) as engine:
        await engine.refresh(
            correlation_id="req-42",
            metadata={"tenant": "acme", "api_token": "do-not-log"},
        )

    counters, _ = metrics.snapshot()
    print("metrics:")
    for name in sorted(counters):
        print(f"  {name} = {counters[name]}")
    print(f"completed events: {len(completed)}, handler errors: {len(events.errors)}")


if __name__ == "__main__":
    asyncio.run(main())
