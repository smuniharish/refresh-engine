"""Starting point for a StateStoreContract test.

Adapt the contract's setup hook to the installed refresh-engine version.
"""

from refresh_engine.testing import StateStoreContract


class TestCustomStateStore(StateStoreContract):
    """Provide the contract setup required by the installed version."""
