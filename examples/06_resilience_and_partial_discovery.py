"""Retry, timeout, best-effort, and partial-discovery safety scenarios."""

import asyncio

from common import MemorySource

from refresh_engine import (
    FailurePolicy,
    RefreshConfig,
    RefreshEngine,
    RetryConfig,
    TimeoutConfig,
)


async def main() -> None:
    source = MemorySource({"alpha": 1, "beta": 2})
    attempts: dict[str, int] = {}

    async def flaky(resource, snapshot, action, request):
        attempts[resource.resource_id] = attempts.get(resource.resource_id, 0) + 1
        if resource.resource_id == "alpha" and attempts[resource.resource_id] == 1:
            raise OSError("transient")
        await asyncio.sleep(0)

    config = RefreshConfig(
        failure_policy=FailurePolicy.RETRY_THEN_CONTINUE,
        retry=RetryConfig(max_attempts=2, initial_delay=0),
        timeout=TimeoutConfig(resource_refresh=1),
    )
    engine = RefreshEngine(source, flaky, config=config)
    result = await engine.refresh()

    source.complete = False
    del source.values["beta"]
    partial = await engine.refresh()
    print(
        f"retry: status={result.status.value}, attempts={attempts}; "
        f"partial discovery deletions={partial.deleted_count}"
    )
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
