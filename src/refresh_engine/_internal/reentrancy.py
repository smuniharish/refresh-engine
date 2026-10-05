"""Detect tasks that a refresh waits for.

A refresh marks the task it runs in, and the worker tasks it starts claim the same
mark. A call that would wait for a refresh can therefore tell whether the current
task is one that the refresh itself waits for, where waiting could never finish.
Tasks started by user code carry the mark of their creator, not their own, so they
are never mistaken for the refresh's tasks.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any, Final

_TASK: Final[ContextVar[tuple[object, asyncio.Task[Any] | None] | None]] = ContextVar(
    "refresh_engine_task", default=None
)


def bind(owner: object) -> None:
    """Mark the current task as one that `owner` waits for."""
    _TASK.set((owner, asyncio.current_task()))


def claim() -> None:
    """Make the current task one that the owner of its creator waits for."""
    task = _TASK.get()
    if task is not None:
        _TASK.set((task[0], asyncio.current_task()))


def awaited_by(owner: object) -> bool:
    """Return whether the current task is one that `owner` waits for."""
    task = _TASK.get()
    return task is not None and task[0] is owner and task[1] is asyncio.current_task()
