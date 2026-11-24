"""Redaction of credentials in structured fields, free text, and exceptions."""

from __future__ import annotations

import logging
import re
import traceback
from collections.abc import Mapping
from typing import Any, Final

REDACTED: Final = "[REDACTED]"

_SENSITIVE_PARTS: Final = re.compile(
    r"pass(?:word|wd|phrase)|secret|token|api_?key|authori[sz]ation|cookie"
    r"|credential|private_?key|session_?(?:id|key)|bearer|connection_?string"
)
_SENSITIVE_WORDS: Final = re.compile(r"(?:^|_)(?:auth|pwd|dsn)(?:$|_)")
_WORD_BOUNDARIES: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SEPARATORS: Final = re.compile(r"[\s.\-]+")

_URL_USERINFO: Final = re.compile(
    r"(?P<scheme>\b[A-Za-z][A-Za-z0-9+.\-]*://)[^\s/@'\"<>]+@"
)
_AUTH_SCHEME: Final = re.compile(
    r"\b(?P<scheme>Bearer|Basic|Digest|Token)(?P<space>\s+)[A-Za-z0-9\-._~+/=:]+",
    re.IGNORECASE,
)
_PAIR: Final = re.compile(
    r"(?P<key>[A-Za-z_][\w.\-]*)(?P<quote>[\"']?)(?P<separator>\s*[:=]\s*)"
    r"(?:(?P<open>[\"'])(?P<quoted>.*?)(?P=open)|(?P<bare>[^\s&,;'\"}\])]+))"
)


def is_sensitive(key: object) -> bool:
    """Whether `key` names a credential-like value."""
    words = _WORD_BOUNDARIES.sub("_", str(key))
    normalized = _SEPARATORS.sub("_", words.lower())
    return bool(
        _SENSITIVE_PARTS.search(normalized) or _SENSITIVE_WORDS.search(normalized)
    )


def redact_fields(values: Mapping[str, Any]) -> dict[str, Any]:
    """Replace the values of credential-like keys, recursing into containers."""
    return {
        key: REDACTED if is_sensitive(key) else _redact_value(value)
        for key, value in values.items()
    }


def _redact_value(value: object) -> object:
    if isinstance(value, Mapping):
        return redact_fields(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def _redact_pairs(text: str) -> str:
    parts: list[str] = []
    position = 0
    while (match := _PAIR.search(text, position)) is not None:
        if is_sensitive(match["key"]):
            head = f"{match['key']}{match['quote']}{match['separator']}"
            opening = match["open"]
            value = REDACTED if opening is None else f"{opening}{REDACTED}{opening}"
            parts.append(text[position : match.start()] + head + value)
            position = match.end()
        else:
            # Resume after the separator so the value can itself start a pair,
            # as in "RuntimeError: password=...".
            parts.append(text[position : match.end("separator")])
            position = match.end("separator")
    parts.append(text[position:])
    return "".join(parts)


def redact_text(text: str) -> str:
    """Redact credentials embedded in free text.

    Replaces the user information of URLs (`scheme://user:password@host`), the
    credentials of `Bearer`, `Basic`, `Digest`, and `Token` authorization values,
    and the values of credential-like `key=value` and `key: value` pairs,
    including quoted keys and values as written by `repr` and JSON.
    """
    text = _URL_USERINFO.sub(rf"\g<scheme>{REDACTED}@", text)
    text = _AUTH_SCHEME.sub(rf"\g<scheme>\g<space>{REDACTED}", text)
    return _redact_pairs(text)


def redact_exception(error: BaseException) -> str:
    """Format `error` with its traceback and chained exceptions, then redact it."""
    rendered = "".join(traceback.TracebackException.from_exception(error).format())
    return redact_text(rendered.rstrip("\n"))


def withheld(error: BaseException) -> str:
    """Describe `error` without any of its text."""
    return f"{type(error).__qualname__}: [message withheld: redaction failed]"


def log_failure(
    logger: logging.Logger,
    level: int,
    message: str,
    *args: object,
    error: BaseException,
) -> None:
    """Log `message` followed by the redacted traceback of `error`."""
    try:
        details = redact_exception(error)
    except Exception:
        details = withheld(error)
    logger.log(level, f"{message}\n%s", *args, details)
