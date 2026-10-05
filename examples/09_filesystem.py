"""Files as resources, with blocking I/O kept off the event loop."""

import asyncio
import os
import tempfile
from pathlib import Path

from refresh_engine import (
    DiscoveryResult,
    PlanAction,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceSnapshot,
)


class DirectorySource:
    """Discover the files below `root`; size and mtime act as a cheap indicator."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(await asyncio.to_thread(self._scan))

    def _scan(self) -> list[Resource]:
        resources = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                stat = path.stat()
                resources.append(
                    Resource(
                        path.relative_to(self.root).as_posix(),
                        cheap_indicator=f"{stat.st_size}:{stat.st_mtime_ns}",
                        cheap_indicator_reliable=True,
                    )
                )
        return resources

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        path = self.root / resource.resource_id
        content = await asyncio.to_thread(path.read_bytes)
        return ResourceSnapshot(resource.resource_id, content=content)


class Index:
    """Keep the size of every indexed file."""

    def __init__(self) -> None:
        self.sizes: dict[str, int] = {}

    async def __call__(
        self,
        resource: Resource,
        snapshot: ResourceSnapshot | None,
        action: PlanAction,
        request: RefreshRequest,
        /,
    ) -> None:
        if action is PlanAction.DELETE:
            self.sizes.pop(resource.resource_id, None)
        elif snapshot is not None:
            self.sizes[resource.resource_id] = len(snapshot.content)


def touch(path: Path, text: str, mtime_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(mtime_ns, mtime_ns))


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        touch(root / "readme.txt", "hello", 1_000_000_000)
        touch(root / "docs" / "guide.md", "# Guide", 1_000_000_000)
        index = Index()
        async with RefreshEngine(DirectorySource(root), index) as engine:
            first = await engine.refresh()
            print(
                f"indexed:  {first.refreshed_count} files {sorted(index.sizes.items())}"
            )

            unchanged = await engine.refresh()
            print(f"rescan:   unchanged={unchanged.unchanged_count}")

            touch(root / "readme.txt", "hello world", 2_000_000_000)
            (root / "docs" / "guide.md").unlink()
            changed = await engine.refresh()
            print(
                f"changes:  modified={changed.modified_count}, "
                f"deleted={changed.deleted_count} {sorted(index.sizes.items())}"
            )


if __name__ == "__main__":
    asyncio.run(main())
