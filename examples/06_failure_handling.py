"""Retries, timeouts, safe partial discovery, fail-fast, and automatic retries."""

import asyncio

from common import DictionarySource

from refresh_engine import (
    FailurePolicy,
    PlanAction,
    RefreshConfig,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
    RetryConfig,
    TimeoutConfig,
)


class FlakyDestination:
    """Fail the first `failures` writes of each resource in `flaky`."""

    def __init__(self, failures: int, flaky: set[str] | None = None) -> None:
        self.failures = failures
        self.flaky = flaky
        self.attempts: dict[str, int] = {}

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        resource_id = resource.resource_id
        attempt = self.attempts.get(resource_id, 0) + 1
        self.attempts[resource_id] = attempt
        if (self.flaky is None or resource_id in self.flaky) and (
            attempt <= self.failures
        ):
            raise ConnectionError(f"attempt {attempt} failed")


async def main() -> None:
    retrying = RefreshConfig(
        retry=RetryConfig(max_attempts=3, initial_delay=0.01, jitter=0.01),
        timeout=TimeoutConfig(operation=5, overall=60),
    )
    destination = FlakyDestination(failures=2)
    source = DictionarySource({"alpha": 1, "beta": 1})
    async with RefreshEngine(source, destination, config=retrying) as engine:
        result = await engine.refresh()
        attempts = {item.resource_id: item.attempts for item in result.executions}
        print(f"retries:          {result.status}, attempts={attempts}")

        source.complete = False
        del source.contents["beta"]
        partial = await engine.refresh()
        print(f"partial listing:  deleted={partial.deleted_count}")
        print(f"                  warning: {partial.warnings[0]}")

    destination = FlakyDestination(failures=1, flaky={"beta"})
    fail_fast = RefreshConfig(failure_policy=FailurePolicy.FAIL_FAST, max_concurrency=1)
    source = DictionarySource({"alpha": 1, "beta": 1, "gamma": 1})
    async with RefreshEngine(source, destination, config=fail_fast) as engine:
        stopped = await engine.refresh()
        (issue,) = stopped.errors
        print(f"fail-fast:        {stopped.status}, started={len(stopped.executions)}")
        print(f"                  {issue.resource_id}: {issue.message}")
        state = await engine.store.load_all()
        print(f"                  persisted states={len(state)}")

        recovered = await engine.refresh()
        print(
            f"next refresh:     {recovered.status}, "
            f"refreshed={recovered.refreshed_count}"
        )


if __name__ == "__main__":
    asyncio.run(main())
