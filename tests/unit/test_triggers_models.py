from __future__ import annotations

import pytest

from refresh_engine import (
    EventTrigger,
    QueryTrigger,
    RefreshConfig,
    RefreshMode,
    RefreshRequest,
    Resource,
    RetryConfig,
    TimeoutConfig,
    TriggerSource,
)


def test_event_and_query_triggers_normalize_requests() -> None:
    event_trigger = EventTrigger(
        lambda event: RefreshRequest(correlation_id=str(event))
    )
    event_request = event_trigger.request_for(42)
    assert event_request is not None
    assert event_request.correlation_id == "42"
    query = QueryTrigger().request(["a"], metadata={"reason": "query"})
    assert query.mode is RefreshMode.TARGETED
    assert query.trigger is TriggerSource.QUERY
    assert query.resource_ids == {"a"}


def test_request_merge_and_configuration_validation() -> None:
    merged = RefreshRequest(
        mode=RefreshMode.INCREMENTAL, resource_ids=frozenset({"a"})
    ).merge(RefreshRequest(mode=RefreshMode.FULL, resource_ids=frozenset({"b"})))
    assert merged.mode is RefreshMode.FULL
    assert merged.resource_ids == {"a", "b"}
    with pytest.raises(ValueError):
        RefreshRequest(mode=RefreshMode.TARGETED)
    with pytest.raises(ValueError):
        RetryConfig(max_attempts=0)
    with pytest.raises(ValueError):
        RetryConfig(exponential_base=0)
    with pytest.raises(ValueError):
        RefreshConfig(timeout=TimeoutConfig(discovery=-1))


def test_resource_identity_validation() -> None:
    with pytest.raises(ValueError):
        Resource("")
