"""Lifecycle events, structured logging, redaction, and metrics scenarios."""

import asyncio

import structlog
from common import MemorySource, Recorder

from refresh_engine import EventPublisher, RefreshEngine
from refresh_engine.observability.logging import StructlogEventHandler
from refresh_engine.observability.metrics import InMemoryMetrics


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    events = EventPublisher()
    metrics = InMemoryMetrics()
    events.subscribe(StructlogEventHandler())

    def count_event(event) -> None:
        metrics.increment(f"event.{event.kind.value}")

    events.subscribe(count_event)

    engine = RefreshEngine(MemorySource({"alpha": 1}), Recorder(), events=events)
    result = await engine.refresh(metadata={"token": "consumer-owned"})
    await events.drain()
    counters, _ = metrics.snapshot()
    assert counters
    print(
        f"summary: status={result.status.value}, event_kinds={len(counters)}, "
        f"handler_errors={len(events.errors)}"
    )
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
