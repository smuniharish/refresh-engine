"""Execute the Python examples published in the main examples guide."""

from __future__ import annotations

import re
from pathlib import Path


def main() -> None:
    guide = Path("docs/guides/examples-and-results.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", guide, flags=re.DOTALL)
    if not blocks:
        raise RuntimeError("no Python examples found in the examples guide")
    exec(compile("\n".join(blocks), "examples-and-results.md", "exec"), {})


if __name__ == "__main__":
    main()
