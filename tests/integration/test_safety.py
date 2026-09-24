from __future__ import annotations

import pytest

from refresh_engine import RefreshEngine, RefreshStatus
from tests.helpers import MutableSource, RecordingOperation


@pytest.mark.asyncio
async def test_source_failure_never_infers_deletion() -> None:
    source = MutableSource({"a": 1, "b": 2})
    engine = RefreshEngine(source, RecordingOperation())
    await engine.refresh()
    before = await engine.store.load_all()
    source.fail_discovery = True
    result = await engine.refresh()
    assert result.status is RefreshStatus.FAILED
    assert result.deleted_count == 0
    assert await engine.store.load_all() == before
    await engine.close()


@pytest.mark.asyncio
async def test_partial_discovery_never_infers_deletion() -> None:
    source = MutableSource({"a": 1, "b": 2})
    engine = RefreshEngine(source, RecordingOperation())
    await engine.refresh()
    del source.values["b"]
    source.complete = False
    result = await engine.refresh()
    assert result.deleted_count == 0
    assert "b" in await engine.store.load_all()
    assert result.warnings
    await engine.close()


@pytest.mark.asyncio
async def test_snapshot_failure_preserves_prior_state() -> None:
    source = MutableSource({"a": 1}, reliable_indicators=False)
    engine = RefreshEngine(source, RecordingOperation())
    await engine.refresh()
    before = (await engine.store.load_all())["a"]
    source.values["a"] = 2
    source.fail_snapshot.add("a")
    result = await engine.refresh()
    assert result.failed_count == 1
    assert (await engine.store.load_all())["a"] == before
    await engine.close()
