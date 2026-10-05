"""Tests for the exception hierarchy in `refresh_engine.errors`."""

from __future__ import annotations

import pickle

import pytest

import refresh_engine
from refresh_engine import (
    ConfigurationError,
    DependencyCycleError,
    DiscoveryError,
    EngineClosedError,
    FingerprintError,
    RefreshError,
    SchedulerError,
    SnapshotError,
    StateStoreError,
)
from refresh_engine import errors as errors_module

ERROR_TYPES: tuple[type[RefreshError], ...] = (
    ConfigurationError,
    DependencyCycleError,
    DiscoveryError,
    EngineClosedError,
    FingerprintError,
    SchedulerError,
    SnapshotError,
    StateStoreError,
)


def test_refresh_error_is_an_exception() -> None:
    assert issubclass(RefreshError, Exception)


@pytest.mark.parametrize("error_type", ERROR_TYPES, ids=lambda kind: kind.__name__)
def test_every_error_derives_from_refresh_error(error_type: type[RefreshError]) -> None:
    assert issubclass(error_type, RefreshError)


def test_configuration_error_is_also_a_value_error() -> None:
    with pytest.raises(ValueError, match="bad value"):
        raise ConfigurationError("bad value")


@pytest.mark.parametrize(
    "error_type",
    [error for error in ERROR_TYPES if error is not ConfigurationError],
    ids=lambda kind: kind.__name__,
)
def test_only_configuration_error_is_a_value_error(
    error_type: type[RefreshError],
) -> None:
    assert not issubclass(error_type, ValueError)


def test_every_error_is_exported_from_the_package_root() -> None:
    for name in errors_module.__all__:
        assert getattr(refresh_engine, name) is getattr(errors_module, name)
        assert name in refresh_engine.__all__


def test_dependency_cycle_error_describes_the_cycle() -> None:
    error = DependencyCycleError(["a", "b", "a"])

    assert str(error) == "dependency cycle detected: a -> b -> a"
    assert error.cycle == ("a", "b", "a")
    assert error.args == ("dependency cycle detected: a -> b -> a",)


def test_dependency_cycle_error_copies_the_cycle_into_a_tuple() -> None:
    cycle = ["a", "a"]
    error = DependencyCycleError(cycle)
    cycle.append("b")

    assert error.cycle == ("a", "a")


def test_dependency_cycle_error_survives_pickling() -> None:
    error = DependencyCycleError(("a", "b", "c", "a"))

    restored = pickle.loads(pickle.dumps(error))  # noqa: S301 - data created here

    assert type(restored) is DependencyCycleError
    assert restored.cycle == ("a", "b", "c", "a")
    assert str(restored) == "dependency cycle detected: a -> b -> c -> a"


@pytest.mark.parametrize(
    "error_type",
    [error for error in ERROR_TYPES if error is not DependencyCycleError],
    ids=lambda kind: kind.__name__,
)
def test_message_errors_survive_pickling(error_type: type[RefreshError]) -> None:
    error = error_type("something failed")

    restored = pickle.loads(pickle.dumps(error))  # noqa: S301 - data created here

    assert type(restored) is error_type
    assert str(restored) == "something failed"
