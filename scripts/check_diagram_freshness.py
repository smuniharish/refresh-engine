"""Fail when generated diagram images are missing or out of date with their sources.

Freshness is content based: `render_diagrams.py` records the SHA-256 of every
line-ending-normalized Mermaid source in `sources.sha256` after rendering.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAGRAMS = ROOT / "docs" / "assets" / "diagrams"
MANIFEST = DIAGRAMS / "sources.sha256"
THEMES = ("light", "dark")


def images(source: Path) -> list[Path]:
    return [source.with_name(f"{source.stem}-{variant}.png") for variant in THEMES]


def sources() -> list[Path]:
    return sorted(DIAGRAMS.glob("*.mmd"))


def digest(source: Path) -> str:
    text = source.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(text).hexdigest()


def write_manifest() -> None:
    lines = [f"{digest(source)}  {source.name}\n" for source in sources()]
    MANIFEST.write_text("".join(lines), encoding="utf-8", newline="\n")


def read_manifest() -> dict[str, str]:
    if not MANIFEST.exists():
        return {}
    recorded = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        value, _, name = line.partition("  ")
        recorded[name] = value
    return recorded


def check() -> int:
    current = sources()
    recorded = read_manifest()
    expected = {image for source in current for image in images(source)}
    problems = []
    for source in current:
        if recorded.get(source.name) != digest(source):
            problems.append(f"stale images: {source.name} changed since rendering")
        problems.extend(
            f"missing image: {image.name}"
            for image in images(source)
            if not image.exists()
        )
    names = {source.name for source in current}
    problems.extend(
        f"manifest entry without a source: {name}"
        for name in sorted(recorded.keys() - names)
    )
    problems.extend(
        f"image without a source: {image.name}"
        for image in sorted(DIAGRAMS.glob("*.png"))
        if image not in expected
    )
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print("run: uv run python scripts/render_diagrams.py", file=sys.stderr)
        return 1
    print(f"{len(current)} diagrams have fresh light and dark images")
    return 0


if __name__ == "__main__":
    sys.exit(check())
