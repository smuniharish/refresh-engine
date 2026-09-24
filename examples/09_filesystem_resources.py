"""Filesystem-backed resources with blocking I/O isolated from the event loop."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


async def _stream(resources: list[Resource]) -> AsyncIterator[Resource]:
    for resource in resources:
        yield resource


class FilesystemSource:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def discover(self) -> DiscoveryResult:
        paths = await asyncio.to_thread(
            lambda: sorted(path for path in self.root.rglob("*") if path.is_file())
        )
        resources = []
        for path in paths:
            stat = await asyncio.to_thread(path.stat)
            resources.append(
                Resource(
                    path.relative_to(self.root).as_posix(),
                    cheap_indicator=f"{stat.st_mtime_ns}:{stat.st_size}",
                    cheap_indicator_reliable=True,
                )
            )
        return DiscoveryResult(_stream(resources))

    async def snapshot(self, resource: Resource) -> ResourceSnapshot:
        content = await asyncio.to_thread((self.root / resource.resource_id).read_bytes)
        return ResourceSnapshot(resource.resource_id, content=content)


async def record(
    resource: Resource | None,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
) -> None:
    print(action.value, resource.resource_id if resource else "deleted")


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        await asyncio.to_thread((root / "document.txt").write_text, "version 1")
        engine = RefreshEngine(FilesystemSource(root), record)
        first = await engine.refresh()
        second = await engine.refresh()
        assert first.refreshed_count == 1
        assert second.refreshed_count == 0
        print(
            f"filesystem: first_refreshed={first.refreshed_count}, "
            f"second_refreshed={second.refreshed_count}"
        )
        await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
