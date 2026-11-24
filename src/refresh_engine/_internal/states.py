"""Validation of state mappings crossing the state-store boundary."""

from __future__ import annotations

from collections.abc import Mapping

from refresh_engine.errors import ConfigurationError
from refresh_engine.models import ResourceState


def check_states(states: object, name: str) -> Mapping[str, ResourceState]:
    """Return `states` after checking that it maps resource IDs to their states.

    Raises:
        ConfigurationError: If `states` is not a mapping, a value is not a
            `ResourceState`, or a key differs from its state's `resource_id`.
    """
    if not isinstance(states, Mapping):
        raise ConfigurationError(f"{name} must be a mapping")
    for key, state in states.items():
        if not isinstance(state, ResourceState):
            raise ConfigurationError(
                f"{name} must map resource IDs to ResourceState objects, "
                f"not {type(state).__name__}"
            )
        if key != state.resource_id:
            raise ConfigurationError(
                f"{name} key {key!r} does not match its state's resource_id "
                f"{state.resource_id!r}"
            )
    return states
