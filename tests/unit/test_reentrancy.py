import asyncio

from refresh_engine._internal.reentrancy import awaited_by, bind, claim


def test_no_task_is_awaited_by_default() -> None:
    assert not awaited_by(object())


async def test_only_bound_and_claimed_tasks_are_awaited_by_their_owner() -> None:
    owner = object()

    async def child(*, claimed: bool) -> bool:
        if claimed:
            claim()
        return awaited_by(owner)

    async def bound() -> list[bool]:
        bind(owner)
        return [
            awaited_by(owner),
            awaited_by(object()),
            await asyncio.create_task(child(claimed=False)),
            await asyncio.create_task(child(claimed=True)),
        ]

    assert await asyncio.create_task(bound()) == [True, False, False, True]
    assert not awaited_by(owner)


async def test_claim_without_an_owner_has_no_effect() -> None:
    async def unowned() -> bool:
        claim()
        return awaited_by(object())

    assert not await asyncio.create_task(unowned())
