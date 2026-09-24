"""Ten-thousand-resource refresh and domain-generic identity demonstration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from common import MemorySource, Recorder

from refresh_engine import RefreshEngine


@dataclass(frozen=True)
class ConsumerResource:
    kind: str
    name: str

    @property
    def resource_id(self) -> str:
        return f"{self.kind}:{self.name}"


async def main() -> None:
    values = {f"synthetic-{index}": index for index in range(10_000)}
    engine = RefreshEngine(MemorySource(values), Recorder())
    await engine.refresh()
    unchanged = await engine.refresh()
    assert unchanged.unchanged_count == 10_000
    assert unchanged.refreshed_count == 0
    await engine.close()

    resources = [
        ConsumerResource("Document", "guide"),
        ConsumerResource("Plugin", "renderer"),
        ConsumerResource("Tool", "formatter"),
        ConsumerResource("Configuration", "production"),
        ConsumerResource("RegistryEntry", "primary"),
    ]
    generic_values = {resource.resource_id: resource.name for resource in resources}
    generic_engine = RefreshEngine(MemorySource(generic_values), Recorder())
    result = await generic_engine.refresh()
    assert result.refreshed_count == len(resources)
    print(
        f"scale: unchanged={unchanged.unchanged_count}; "
        f"generic_resource_kinds={len(resources)}, refreshed={result.refreshed_count}"
    )
    await generic_engine.close()


if __name__ == "__main__":
    asyncio.run(main())
