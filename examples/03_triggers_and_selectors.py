"""Event, query, and reusable selector scenarios."""

import asyncio

from common import MemorySource, Recorder

from refresh_engine import (
    EventTrigger,
    QueryTrigger,
    RefreshEngine,
    RefreshRequest,
    Resource,
    TagSelector,
    TriggerSource,
)


async def main() -> None:
    source = MemorySource({"alpha": 1, "beta": 2})
    engine = RefreshEngine(source, Recorder())

    event_trigger = EventTrigger(
        lambda event: (
            RefreshRequest(trigger=TriggerSource.EVENT) if event == "changed" else None
        )
    )
    event_request = event_trigger.request_for("changed")
    assert event_request is not None
    await engine.submit(event_request)

    query_request = QueryTrigger().request(
        ["beta"],
        metadata={"caller": "demo"},
    )
    result = await engine.submit(query_request)
    selector = TagSelector(["ready"])
    print(
        f"query: refreshed={result.refreshed_count}, "
        f"tag_matches={selector.matches(Resource('x', {'tags': ['ready']}))}"
    )
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
