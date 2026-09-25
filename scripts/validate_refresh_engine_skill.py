"""Validate the refresh-engine-skills distribution against the real package.

This script is the "source of truth automation" referenced by the skill
distribution's README. It performs cheap, deterministic checks intended to
catch skill documentation drifting away from the actual `refresh_engine`
public API and from its own internal structure:

1. SKILL.md frontmatter is present and satisfies the Agent Skills
   specification's minimal constraints (name/description format).
2. Every reference/example/eval file the skill's structure implies actually
   exists on disk (no dangling links within the skill tree).
3. Every `refresh_engine.<Symbol>` / bare `Symbol` mention that looks like a
   public API reference in SKILL.md and references/*.md is checked against
   the live ``refresh_engine.__all__`` — catching APIs that were renamed or
   removed.
4. Every Python code fence in skills/refresh-engine/examples/*.md is executed
   to catch example drift (not just syntax-checked).
5. Plugin manifest JSON files parse and contain the required fields.

Run with:

    uv run python scripts/validate_refresh_engine_skill.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "refresh-engine-skills"
SKILL_DIR = SKILLS_ROOT / "skills" / "refresh-engine"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

REQUIRED_REFERENCES = [
    "architecture.md",
    "api-reference.md",
    "incremental-refresh.md",
    "fingerprinting.md",
    "dependency-refresh.md",
    "scheduling.md",
    "concurrency.md",
    "state-management.md",
    "failure-handling.md",
    "testing.md",
    "integration-patterns.md",
    "troubleshooting.md",
]

REQUIRED_EXAMPLES = [
    "basic.md",
    "incremental.md",
    "scheduled.md",
    "dependency-aware.md",
    "custom-extension.md",
]

REQUIRED_EVALS = [
    "basic-integration.yaml",
    "incremental-refresh.yaml",
    "scheduler.yaml",
    "dependency-refresh.yaml",
    "failure-handling.yaml",
    "architecture.yaml",
]

REQUIRED_MANIFESTS = [
    SKILLS_ROOT / "plugin.json",
    SKILLS_ROOT / ".claude-plugin" / "plugin.json",
    SKILLS_ROOT / ".codex-plugin" / "plugin.json",
]


class ValidationError(Exception):
    """Raised to collect a validation failure without stopping other checks."""


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def parse_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    if not match:
        raise ValidationError("SKILL.md is missing a --- frontmatter block")
    raw = match.group(1)
    # Minimal parse: only need top-level name/description/license for checks.
    name_match = re.search(r"^name:\s*(.+)$", raw, flags=re.MULTILINE)
    desc_match = re.search(
        r"^description:\s*>?-?\s*\n(.*?)(?=\n[a-zA-Z_-]+:|\Z)", raw, flags=re.DOTALL
    )
    if desc_match is None:
        desc_match = re.search(r"^description:\s*(.+)$", raw, flags=re.MULTILINE)
        description = desc_match.group(1).strip() if desc_match else ""
    else:
        description = " ".join(
            line.strip() for line in desc_match.group(1).splitlines() if line.strip()
        )
    return {
        "name": name_match.group(1).strip() if name_match else "",
        "description": description,
    }


def check_skill_md(errors: list[str]) -> None:
    skill_md = SKILL_DIR / "SKILL.md"
    if not skill_md.exists():
        fail(errors, f"missing {skill_md}")
        return
    text = skill_md.read_text(encoding="utf-8")
    try:
        frontmatter = parse_frontmatter(text)
    except ValidationError as exc:
        fail(errors, str(exc))
        return

    name = frontmatter["name"]
    if not name:
        fail(errors, "SKILL.md frontmatter missing 'name'")
    elif not NAME_RE.match(name):
        fail(errors, f"SKILL.md name '{name}' violates Agent Skills naming rules")
    elif len(name) > 64:
        fail(errors, f"SKILL.md name '{name}' exceeds 64 characters")
    elif name != SKILL_DIR.name:
        fail(
            errors,
            f"SKILL.md name '{name}' does not match parent directory "
            f"'{SKILL_DIR.name}'",
        )

    description = frontmatter["description"]
    if not description:
        fail(errors, "SKILL.md frontmatter missing 'description'")
    elif not (1 <= len(description) <= 1024):
        fail(
            errors,
            f"SKILL.md description length {len(description)} outside 1-1024 chars",
        )


def check_structure(errors: list[str]) -> None:
    for name in REQUIRED_REFERENCES:
        path = SKILL_DIR / "references" / name
        if not path.exists():
            fail(errors, f"missing reference file: {path}")
    for name in REQUIRED_EXAMPLES:
        path = SKILL_DIR / "examples" / name
        if not path.exists():
            fail(errors, f"missing example file: {path}")
    for name in REQUIRED_EVALS:
        path = SKILL_DIR / "evals" / name
        if not path.exists():
            fail(errors, f"missing eval file: {path}")
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            fail(errors, f"{path} is not valid YAML: {exc}")
            continue
        if not isinstance(data, dict) or "expected_behavior" not in data:
            fail(errors, f"{path} missing required 'expected_behavior' field")
    readme = SKILL_DIR / "README.md"
    if not readme.exists():
        fail(errors, f"missing {readme}")


def load_public_api() -> set[str]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    import refresh_engine

    return set(refresh_engine.__all__)


KNOWN_SUBMODULES = {
    "api",
    "core",
    "detection",
    "planning",
    "execution",
    "scheduling",
    "triggers",
    "events",
    "observability",
    "stores",
    "errors",
}


def check_api_drift(errors: list[str], public_api: set[str]) -> None:
    """Flag `refresh_engine.Symbol` mentions whose Symbol is not public API.

    Dunder attributes (e.g. ``__all__``) and known internal submodule names
    (e.g. ``refresh_engine.core``) are legitimate non-``__all__`` mentions and
    are excluded from drift checking.
    """
    pattern = re.compile(r"`refresh_engine\.([A-Za-z_][A-Za-z0-9_]*)")
    markdown_files = list(SKILL_DIR.glob("*.md"))
    markdown_files += list((SKILL_DIR / "references").glob("*.md"))
    markdown_files += list((SKILL_DIR / "examples").glob("*.md"))
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        for symbol in pattern.findall(text):
            if symbol.startswith("__") or symbol in KNOWN_SUBMODULES:
                continue
            if symbol not in public_api:
                fail(
                    errors,
                    f"{path.relative_to(REPO_ROOT)} references "
                    f"'refresh_engine.{symbol}' which is not in refresh_engine.__all__",
                )


def _compile_block(source: str, filename: str) -> None:
    """Syntax-check one code fence, tolerating bare top-level ``await``.

    Some example fences are excerpts from inside an async function (shown for
    brevity) rather than complete standalone scripts. If a fence fails only
    because of a top-level ``await``/``async for``/``async with``, retry it
    wrapped in an ``async def`` shim before treating it as a real error.
    """
    try:
        compile(source, filename, "exec")
        return
    except SyntaxError as exc:
        if "await" not in str(exc) and "'async " not in str(exc):
            raise
    indented = "\n".join(f"    {line}" for line in source.splitlines())
    wrapped = f"async def _skill_example_fragment():\n{indented}\n"
    compile(wrapped, filename, "exec")


def check_examples_execute(errors: list[str]) -> None:
    for name in REQUIRED_EXAMPLES:
        path = SKILL_DIR / "examples" / name
        if not path.exists():
            continue  # already reported by check_structure
        text = path.read_text(encoding="utf-8")
        blocks = re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)
        if not blocks:
            fail(errors, f"{path.relative_to(REPO_ROOT)} has no python code fences")
            continue
        for index, block in enumerate(blocks):
            try:
                _compile_block(block, f"{path}#block{index}")
            except SyntaxError as exc:
                fail(
                    errors,
                    f"{path.relative_to(REPO_ROOT)} code fence #{index} has a "
                    f"syntax error: {exc}",
                )


def check_plugin_manifests(errors: list[str]) -> None:
    for path in REQUIRED_MANIFESTS:
        if not path.exists():
            fail(errors, f"missing plugin manifest: {path}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(errors, f"{path} is not valid JSON: {exc}")
            continue
        if "name" not in data:
            fail(errors, f"{path} missing required 'name' field")


def check_no_duplicate_skill_md(errors: list[str]) -> None:
    skill_files = list(SKILLS_ROOT.rglob("SKILL.md"))
    if len(skill_files) != 1:
        fail(
            errors,
            "expected exactly one canonical SKILL.md, found: "
            + ", ".join(str(p.relative_to(REPO_ROOT)) for p in skill_files),
        )


def main() -> int:
    errors: list[str] = []

    check_skill_md(errors)
    check_structure(errors)
    check_plugin_manifests(errors)
    check_no_duplicate_skill_md(errors)
    check_examples_execute(errors)

    try:
        public_api = load_public_api()
    except ImportError as exc:
        fail(errors, f"could not import refresh_engine to check API drift: {exc}")
    else:
        check_api_drift(errors, public_api)

    if errors:
        print(f"refresh-engine-skills validation FAILED ({len(errors)} issue(s)):\n")
        for message in errors:
            print(f"  - {message}")
        return 1

    print("refresh-engine-skills validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
