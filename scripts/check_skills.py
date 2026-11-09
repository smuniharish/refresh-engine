"""Check the Agent Skills distribution without changing project configuration."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "refresh-engine-skills" / "skills"
EXPECTED = {"refresh-engine", "refresh-engine-extend"}


def fail(message: str) -> None:
    raise ValueError(message)


def check_skill(name: str, all_queries: set[str]) -> None:
    directory = SKILLS_ROOT / name
    skill_file = directory / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        fail(f"{skill_file.relative_to(ROOT)}: missing YAML frontmatter")
    header, separator, body = text[4:].partition("\n---\n")
    if not separator:
        fail(f"{skill_file.relative_to(ROOT)}: unterminated YAML frontmatter")
    if not re.search(rf"(?m)^name:\s*{re.escape(name)}\s*$", header):
        fail(f"{skill_file.relative_to(ROOT)}: name must match directory")
    if not re.search(r'(?m)^\s+version:\s*"1\.0\.0"\s*$', header):
        fail(f"{skill_file.relative_to(ROOT)}: metadata version must be 1.0.0")
    if not re.search(r"(?m)^description:\s*>-$", header):
        fail(f"{skill_file.relative_to(ROOT)}: description must be present")
    if not re.search(r"(?m)^license:\s*Apache-2\.0\s*$", header):
        fail(f"{skill_file.relative_to(ROOT)}: license must be Apache-2.0")

    linked_paths = re.findall(r"\[[^\]]+\]\((\./[^)]+)\)", body)
    for relative in linked_paths:
        target = (directory / relative[2:]).resolve()
        if not target.is_relative_to(directory.resolve()) or not target.exists():
            fail(f"{skill_file.relative_to(ROOT)}: broken link {relative}")
    for reference in (directory / "references").glob("*.md"):
        relative = f"./references/{reference.name}"
        if relative not in linked_paths:
            fail(f"{skill_file.relative_to(ROOT)}: unlinked reference {relative}")
    for asset in (directory / "assets").glob("*"):
        if asset.is_file() and f"./assets/{asset.name}" not in body:
            fail(f"{skill_file.relative_to(ROOT)}: unlinked asset {asset.name}")

    evals_path = directory / "evals" / "evals.json"
    evals = json.loads(evals_path.read_text(encoding="utf-8"))
    if evals.get("skill_name") != name or not evals.get("evals"):
        fail(f"{evals_path.relative_to(ROOT)}: invalid eval collection")
    for case in evals["evals"]:
        for item in case.get("files", []):
            if not (directory / "evals" / item).is_file():
                fail(f"{evals_path.relative_to(ROOT)}: missing fixture {item}")

    triggers_path = directory / "evals" / "trigger_queries.json"
    triggers = json.loads(triggers_path.read_text(encoding="utf-8"))
    if triggers.get("skill_name") != name:
        fail(f"{triggers_path.relative_to(ROOT)}: skill_name mismatch")
    queries = triggers.get("queries", [])
    for split in ("train", "validation"):
        split_queries = [query for query in queries if query.get("split") == split]
        positive = sum(query.get("should_trigger") is True for query in split_queries)
        negative = sum(query.get("should_trigger") is False for query in split_queries)
        if positive != negative or positive == 0:
            fail(f"{triggers_path.relative_to(ROOT)}: unbalanced {split} triggers")
    for query in queries:
        value = query.get("query", "").casefold()
        if not value or value in all_queries:
            fail(f"{triggers_path.relative_to(ROOT)}: duplicate or empty query")
        all_queries.add(value)

    for sample in directory.rglob("*.py"):
        compile(sample.read_text(encoding="utf-8"), str(sample), "exec")

    print(f"PASS {name}")


def main() -> int:
    try:
        manifest_path = ROOT / "skills.sh.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("paths") != ["./refresh-engine-skills/skills"]:
            fail("skills.sh.json must register ./refresh-engine-skills/skills")
        found = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}
        if found != EXPECTED:
            fail(
                f"expected skill directories {sorted(EXPECTED)}, found {sorted(found)}"
            )
        queries: set[str] = set()
        for name in sorted(EXPECTED):
            check_skill(name, queries)
    except (OSError, ValueError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1
    print("All skill distribution checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
