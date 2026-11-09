"""Fail when generated diagram images are missing or older than their sources."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAGRAMS = ROOT / "docs" / "assets" / "diagrams"
THEMES = ("light", "dark")


def images(source: Path) -> list[Path]:
    return [source.with_name(f"{source.stem}-{variant}.png") for variant in THEMES]


def check() -> int:
    sources = sorted(DIAGRAMS.glob("*.mmd"))
    expected = {image for source in sources for image in images(source)}
    problems = []
    for source in sources:
        for image in images(source):
            if not image.exists():
                problems.append(f"missing image: {image.name}")
            elif image.stat().st_mtime_ns < source.stat().st_mtime_ns:
                problems.append(f"stale image: {image.name} (source: {source.name})")
    stray = sorted(
        image.name for image in DIAGRAMS.glob("*.png") if image not in expected
    )
    problems.extend(f"image without a source: {name}" for name in stray)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"{len(sources)} diagrams have fresh light and dark images")
    return 0


if __name__ == "__main__":
    sys.exit(check())
