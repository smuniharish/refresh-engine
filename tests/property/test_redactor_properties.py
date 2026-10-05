from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from refresh_engine import default_redactor

SENSITIVE = (
    "password",
    "db_password",
    "Secret",
    "accessToken",
    "api-key",
    "X-Api-Key",
    "Authorization",
    "cookie",
    "credentials",
    "private_key",
    "session_id",
    "auth",
    "pwd",
    "dsn",
    "dbPwd",
    "sentryDsn",
    "XAPIKey",
)
SAFE = (
    "name",
    "author",
    "authority",
    "authorName",
    "count",
    "status",
    "keyboard",
    "region",
)

keys = st.sampled_from(SENSITIVE + SAFE)
leaves = st.none() | st.integers() | st.text(max_size=5)
structures = st.recursive(
    leaves,
    lambda children: (
        st.dictionaries(keys, children, max_size=4)
        | st.lists(children, max_size=3)
        | st.lists(children, max_size=3).map(tuple)
    ),
    max_leaves=10,
)


def check(original: Any, redacted: Any) -> None:
    if isinstance(original, Mapping):
        assert set(redacted) == set(original)
        for key, value in original.items():
            if key in SENSITIVE:
                assert redacted[key] == "[REDACTED]"
            else:
                check(value, redacted[key])
    elif isinstance(original, list | tuple):
        assert type(redacted) is type(original)
        assert len(redacted) == len(original)
        for before, after in zip(original, redacted, strict=True):
            check(before, after)
    else:
        assert redacted == original


@given(st.dictionaries(keys, structures, max_size=5))
def test_sensitive_values_never_survive_and_everything_else_is_kept(
    values: dict[str, Any],
) -> None:
    check(values, default_redactor(values))
