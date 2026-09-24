"""Run self-contained numbered examples in isolated Python processes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    examples = sorted(Path("examples").glob("[0-9][0-9]_*.py"))
    examples = [path for path in examples if path.name != "14_postgres_state_store.py"]
    if not examples:
        raise RuntimeError("no numbered examples found")

    for example in examples:
        print(f"Running {example}", flush=True)
        subprocess.run([sys.executable, str(example)], check=True)


if __name__ == "__main__":
    main()
