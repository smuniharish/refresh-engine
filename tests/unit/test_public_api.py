from __future__ import annotations

import refresh_engine
from refresh_engine.errors import SchedulerError


def test_package_identity_and_public_api() -> None:
    assert refresh_engine.__version__ == "0.1.0"
    assert refresh_engine.RefreshEngine
    assert refresh_engine.Resource
    assert refresh_engine.AsyncScheduler
    assert refresh_engine.StateStore
    assert refresh_engine.StateTransaction
    assert refresh_engine.EventHandler
    assert refresh_engine.MetricsSink
    assert refresh_engine.StructlogEventHandler
    assert refresh_engine.InMemoryMetrics
    assert refresh_engine.default_redactor
    assert SchedulerError
