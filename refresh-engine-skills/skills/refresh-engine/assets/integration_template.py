"""Template for a production refresh-engine integration.

Copy this file into the application, then adapt every place marked "Adapt:".
As written, it keeps an in-memory search index in sync with an in-memory catalog
and runs one refresh:

    python integration_template.py
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import structlog

from refresh_engine import (
    AsyncScheduler,
    DiscoveryResult,
    EventPublisher,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshRequest,
    RefreshResult,
    Resource,
    ResourceSnapshot,
    RetryConfig,
    StructlogEventHandler,
    TimeoutConfig,
    TriggerSource,
)

# Adapt: replace with a client for the system of record.
CATALOG: dict[str, dict[str, Any]] = {
    "guide": {"title": "Guide", "body": "How to start", "revision": 3, "uses": []},
    "faq": {"title": "FAQ", "body": "Answers", "revision": 7, "uses": ["guide"]},
}

# Adapt: replace with a client for the destination.
SEARCH_INDEX: dict[str, str] = {}


class CatalogSource:
    """Lists and reads catalog entries."""

    async def discover(self) -> DiscoveryResult:
        """List every entry cheaply.

        Adapt: raise when the catalog is unavailable, and pass complete=False when
        the listing may be missing entries.
        """

        async def listing() -> AsyncIterator[Resource]:
            for entry_id, entry in sorted(CATALOG.items()):
                yield Resource(
                    entry_id,
                    dependencies=frozenset(entry["uses"]),
                    cheap_indicator=str(entry["revision"]),
                    cheap_indicator_reliable=True,
                )

        return DiscoveryResult(listing(), complete=True)

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        """Read what the operation needs.

        Adapt: read from the catalog; wrap blocking clients in asyncio.to_thread.
        """
        entry = CATALOG[resource.resource_id]
        return ResourceSnapshot(
            resource.resource_id,
            content={"title": entry["title"], "body": entry["body"]},
            version=str(entry["revision"]),
        )


async def update_search_index(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    """Apply one change to the search index.

    Adapt: write to the destination idempotently, and let errors propagate so that
    the resource is retried.
    """
    if action is PlanAction.DELETE:
        SEARCH_INDEX.pop(resource.resource_id, None)
    elif snapshot is not None:
        content = snapshot.content
        SEARCH_INDEX[resource.resource_id] = f"{content['title']}: {content['body']}"


def build_engine() -> RefreshEngine:
    """Create an engine with production settings."""
    events = EventPublisher()
    events.subscribe(StructlogEventHandler())
    config = RefreshConfig(
        max_concurrency=8,
        retry=RetryConfig(
            max_attempts=3,
            initial_delay=0.5,
            max_delay=10.0,
            jitter=0.5,
            retryable_exceptions=(ConnectionError, TimeoutError),
        ),
        timeout=TimeoutConfig(discovery=60, snapshot=30, operation=30, overall=900),
    )
    # Adapt: pass store=<durable StateStore> so that state survives restarts.
    return RefreshEngine(
        CatalogSource(), update_search_index, config=config, events=events
    )


def summarize(result: RefreshResult) -> str:
    """Describe a refresh result in one line."""
    return (
        f"{result.status}: {result.added_count} added, "
        f"{result.modified_count} modified, {result.deleted_count} deleted, "
        f"{result.failed_count} failed"
    )


async def run_once() -> RefreshResult:
    """Run one refresh, as a batch job does."""
    async with build_engine() as engine:
        return await engine.refresh()


async def serve(shutdown: asyncio.Event, *, interval: float = 300) -> None:
    """Refresh every `interval` seconds until `shutdown` is set, as a service does."""
    engine = build_engine()
    scheduler = AsyncScheduler(
        lambda: engine.refresh(trigger=TriggerSource.SCHEDULED),
        interval=interval,
        events=engine.events,
        run_immediately=True,
    )
    async with engine, scheduler:
        await shutdown.wait()


if __name__ == "__main__":
    # Adapt: configure structlog once at application startup.
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    result = asyncio.run(run_once())
    print(summarize(result))
    for issue in result.errors:
        print(f"  {issue.phase} {issue.resource_id}: {issue.message}")
    print(SEARCH_INDEX)
