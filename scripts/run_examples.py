"""Run the numbered examples and compare their output with the expected output.

Each example must exit successfully, write nothing to standard error, and print
exactly the text stored in `examples/output/<example>.txt`. Examples that need an
external service are skipped unless the corresponding environment variable is set.

Usage:
    python scripts/run_examples.py            # verify every example
    python scripts/run_examples.py --update   # rewrite the expected output
"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
OUTPUT = EXAMPLES / "output"
REQUIRES = {"11_postgres_state_store.py": "REFRESH_ENGINE_POSTGRES_DSN"}


def run(example: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-W", "error", example.name],
        cwd=EXAMPLES,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and verify the examples.")
    parser.add_argument(
        "--update", action="store_true", help="rewrite the expected output files"
    )
    arguments = parser.parse_args()
    failures = 0
    for example in sorted(EXAMPLES.glob("[0-9][0-9]_*.py")):
        variable = REQUIRES.get(example.name)
        if variable is not None and not os.environ.get(variable):
            print(f"skip  {example.name} (set {variable} to run it)")
            continue
        completed = run(example)
        stdout = completed.stdout.replace("\r\n", "\n")
        expected_path = OUTPUT / f"{example.stem}.txt"
        if completed.returncode != 0 or completed.stderr:
            failures += 1
            print(f"FAIL  {example.name} (exit code {completed.returncode})")
            print(completed.stderr)
            continue
        if arguments.update:
            expected_path.write_text(stdout, encoding="utf-8", newline="\n")
            print(f"wrote {expected_path.relative_to(ROOT).as_posix()}")
            continue
        expected = (
            expected_path.read_text(encoding="utf-8") if expected_path.exists() else ""
        )
        if stdout == expected:
            print(f"ok    {example.name}")
            continue
        failures += 1
        print(f"FAIL  {example.name} printed unexpected output:")
        sys.stdout.writelines(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                stdout.splitlines(keepends=True),
                "expected",
                "actual",
            )
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
