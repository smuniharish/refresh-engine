"""Minimal provider-neutral metrics implementation."""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Mapping

from refresh_engine.api.abc import MetricsSinkABC


class InMemoryMetrics(MetricsSinkABC):
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._observations: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def increment(
        self, name: str, value: int = 1, attributes: Mapping[str, str] | None = None
    ) -> None:
        key = self._key(name, attributes)
        with self._lock:
            self._counters[key] += value

    def observe(
        self, name: str, value: float, attributes: Mapping[str, str] | None = None
    ) -> None:
        key = self._key(name, attributes)
        with self._lock:
            self._observations[key].append(value)

    def snapshot(self) -> tuple[dict[str, int], dict[str, tuple[float, ...]]]:
        with self._lock:
            return (
                dict(self._counters),
                {key: tuple(values) for key, values in self._observations.items()},
            )

    @staticmethod
    def _key(name: str, attributes: Mapping[str, str] | None) -> str:
        if not attributes:
            return name
        suffix = ",".join(f"{key}={value}" for key, value in sorted(attributes.items()))
        return f"{name}{{{suffix}}}"
