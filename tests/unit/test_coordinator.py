from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from refresh_engine import (
    IDSelector,
    OverlapPolicy,
    RefreshMode,
    RefreshRequest,
    RefreshResult,
    RefreshStatus,
    Resource,
    TagSelector,
    TriggerSource,
)
from refresh_engine._internal.context import RunTicket, unrun_result
from refresh_engine._internal.coordinator import (
    SUPERSEDED,
    RefreshCoordinator,
    _Entry,
    coalesce,
)

FULL = RefreshMode.FULL
INCREMENTAL = RefreshMode.INCREMENTAL
DEPENDENCY_AWARE = RefreshMode.DEPENDENCY_AWARE


def request(mode: RefreshMode = INCREMENTAL) -> RefreshRequest:
    if mode is RefreshMode.TARGETED:
        return RefreshRequest(mode=mode, resource_ids={"a"})
    return RefreshRequest(mode=mode)


def merge(first: RefreshRequest, second: RefreshRequest) -> RefreshRequest:
    merged = coalesce(first, second)
    assert merged is not None
    return merged


@pytest.mark.parametrize(
    ("first", "second", "merged"),
    [
        (INCREMENTAL, INCREMENTAL, INCREMENTAL),
        (INCREMENTAL, DEPENDENCY_AWARE, DEPENDENCY_AWARE),
        (DEPENDENCY_AWARE, INCREMENTAL, DEPENDENCY_AWARE),
        (DEPENDENCY_AWARE, DEPENDENCY_AWARE, DEPENDENCY_AWARE),
        (FULL, INCREMENTAL, FULL),
        (INCREMENTAL, FULL, FULL),
        (FULL, RefreshMode.TARGETED, FULL),
        (RefreshMode.TARGETED, FULL, FULL),
        (FULL, FULL, FULL),
        (RefreshMode.TARGETED, RefreshMode.TARGETED, RefreshMode.TARGETED),
        (INCREMENTAL, RefreshMode.TARGETED, None),
        (RefreshMode.TARGETED, DEPENDENCY_AWARE, None),
    ],
)
def test_coalesce_merges_only_when_one_request_covers_the_other(
    first: RefreshMode, second: RefreshMode, merged: RefreshMode | None
) -> None:
    result = coalesce(request(first), request(second))
    assert (result.mode if result is not None else None) is merged


def test_coalesce_keeps_the_first_identity_and_merges_metadata() -> None:
    early = datetime(2026, 1, 1, tzinfo=UTC)
    first = RefreshRequest(
        trigger=TriggerSource.SCHEDULED,
        metadata={"a": 1, "shared": "first"},
        requested_at=early + timedelta(seconds=1),
    )
    second = RefreshRequest(
        trigger=TriggerSource.EVENT,
        metadata={"b": 2, "shared": "second"},
        correlation_id="second",
        requested_at=early,
    )
    merged = merge(first, second)
    assert merged.trigger is TriggerSource.SCHEDULED
    assert merged.metadata == {"a": 1, "b": 2, "shared": "second"}
    assert merged.correlation_id == "second"
    assert merged.requested_at == early
    with_id = RefreshRequest(correlation_id="first")
    assert merge(with_id, second).correlation_id == "first"


def test_coalescing_targeted_requests_unions_ids_and_selectors() -> None:
    hot = TagSelector("hot")
    cold = TagSelector("cold")
    by_ids = RefreshRequest(mode=RefreshMode.TARGETED, resource_ids={"a"})
    by_hot = RefreshRequest(mode=RefreshMode.TARGETED, selector=hot, resource_ids={"b"})
    by_cold = RefreshRequest(mode=RefreshMode.TARGETED, selector=cold)

    merged = merge(by_ids, by_hot)
    assert merged.resource_ids == {"a", "b"}
    assert merged.selector is hot

    assert merge(by_hot, by_ids).selector is hot
    assert merge(by_hot, by_hot).selector is hot

    union = merge(by_hot, by_cold).selector
    assert union is not None
    assert union.matches(Resource("x", metadata={"tags": ["cold"]}))
    assert union.matches(Resource("x", metadata={"tags": ["hot"]}))
    assert not union.matches(Resource("x"))


def test_coalescing_with_full_drops_targeting() -> None:
    targeted = RefreshRequest(
        mode=RefreshMode.TARGETED, resource_ids={"a"}, selector=IDSelector("b")
    )
    merged = merge(targeted, RefreshRequest(mode=FULL))
    assert (merged.resource_ids, merged.selector) == (frozenset(), None)


def finish(ticket: RunTicket, status: RefreshStatus, reason: str) -> RefreshResult:
    return unrun_result(ticket, status, reason)


async def test_a_runner_cancelled_without_a_result_reports_the_cancel_reason() -> None:
    started = asyncio.Event()

    async def run(ticket: RunTicket) -> RefreshResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    coordinator = RefreshCoordinator(run, finish, OverlapPolicy.CANCEL_PREVIOUS)
    first = asyncio.create_task(coordinator.submit(RefreshRequest()))
    await asyncio.wait_for(started.wait(), timeout=5)
    started.clear()
    second = asyncio.create_task(coordinator.submit(RefreshRequest()))
    await asyncio.wait_for(started.wait(), timeout=5)
    result = await first
    assert result.status is RefreshStatus.CANCELLED
    assert result.warnings == (SUPERSEDED,)
    await coordinator.close(cancel=True)
    assert (await second).status is RefreshStatus.CANCELLED


async def test_runner_exceptions_reach_every_waiter() -> None:
    release = asyncio.Event()

    async def run(ticket: RunTicket) -> RefreshResult:
        await release.wait()
        raise ValueError("runner bug")

    coordinator = RefreshCoordinator(run, finish, OverlapPolicy.COALESCE)
    first = asyncio.create_task(coordinator.submit(RefreshRequest()))
    await asyncio.sleep(0)
    second = asyncio.create_task(coordinator.submit(RefreshRequest()))
    third = asyncio.create_task(coordinator.submit(RefreshRequest()))
    await asyncio.sleep(0)
    release.set()
    for task in (first, second, third):
        with pytest.raises(ValueError, match="runner bug"):
            await task
    await coordinator.close(cancel=False)


async def test_abandoning_an_unknown_waiter_is_harmless() -> None:
    async def run(ticket: RunTicket) -> RefreshResult:
        return unrun_result(ticket, RefreshStatus.SUCCESS, "")

    coordinator = RefreshCoordinator(run, finish, OverlapPolicy.QUEUE)
    coordinator._abandon(asyncio.get_running_loop().create_future())
    coordinator._cancel_active("nothing is running")
    result = await coordinator.submit(RefreshRequest())
    assert result.status is RefreshStatus.SUCCESS
    await coordinator.close(cancel=True)
    assert coordinator.closed


async def test_waiters_that_already_finished_are_not_resolved_again() -> None:
    loop = asyncio.get_running_loop()
    finished: asyncio.Future[RefreshResult] = loop.create_future()
    finished.cancel()
    waiting: asyncio.Future[RefreshResult] = loop.create_future()
    failing: asyncio.Future[RefreshResult] = loop.create_future()
    ticket = RunTicket(RefreshRequest())
    result = unrun_result(ticket, RefreshStatus.SUCCESS, "")

    RefreshCoordinator._resolve(_Entry(ticket, [finished, waiting]), result)
    RefreshCoordinator._reject(_Entry(ticket, [finished, failing]), KeyError("x"))

    assert waiting.result() is result
    assert isinstance(failing.exception(), KeyError)
    assert finished.cancelled()


async def test_a_later_queued_caller_can_leave_without_affecting_earlier_ones() -> None:
    release = asyncio.Event()
    runs: list[RefreshMode] = []

    async def run(ticket: RunTicket) -> RefreshResult:
        runs.append(ticket.request.mode)
        await release.wait()
        return unrun_result(ticket, RefreshStatus.SUCCESS, "")

    coordinator = RefreshCoordinator(run, finish, OverlapPolicy.QUEUE)
    active = asyncio.create_task(coordinator.submit(RefreshRequest()))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    second = asyncio.create_task(coordinator.submit(RefreshRequest(mode=FULL)))
    third = asyncio.create_task(
        coordinator.submit(RefreshRequest(mode=DEPENDENCY_AWARE))
    )
    await asyncio.sleep(0)
    third.cancel()
    with pytest.raises(asyncio.CancelledError):
        await third
    release.set()
    await asyncio.gather(active, second)
    await coordinator.close(cancel=False)
    assert runs == [INCREMENTAL, FULL]
