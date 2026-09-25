# refresh-engine skill

This directory is the **canonical** Agent Skills package for the
[`refresh-engine`](https://pypi.org/project/refresh-engine/) Python library
(import `refresh_engine`). It follows the open
[Agent Skills specification](https://agentskills.io/specification).

## Purpose

Teach AI coding agents how to correctly integrate, configure, extend, debug,
and test `refresh-engine` — using only its real, verified public API — rather
than guessing or hallucinating behavior.

## Supported agents

This skill is a plain `SKILL.md` + `references/` + `examples/` + `evals/`
directory, which is natively understood by every host that implements the
open Agent Skills spec, including:

- **Claude Code** — via project auto-discovery (`.claude/skills/`) or via the
  plugin manifest in [`../.claude-plugin/plugin.json`](../.claude-plugin/plugin.json).
- **Codex CLI / ChatGPT** — via repository auto-discovery (`.agents/skills/`);
  see [`agents/openai.yaml`](agents/openai.yaml) for optional Codex-specific
  metadata.
- **GitHub Copilot** (CLI, agent mode, cloud agent, code review) — via
  `.github/skills/`, `.claude/skills/`, or `.agents/skills/`.

See the [repository root README](../README.md) for exactly which paths are
wired up in this repository and why.

## Scope

- In scope: everything needed to use `refresh_engine` correctly as an
  application dependency — the engine, its contracts, refresh modes,
  fingerprinting, dependency-aware refresh, scheduling, concurrency, state
  management, failure handling, testing, and integration patterns.
- Out of scope: this skill does not teach or assume any particular resource
  domain (MCP, agents, files, etc.) — see
  [SKILL.md](SKILL.md#core-mental-model-lifecycle) and
  [references/troubleshooting.md](references/troubleshooting.md) for why that
  boundary matters.

## Repository structure

```
skills/refresh-engine/
├── SKILL.md            # canonical, concise entry point (load this first)
├── README.md            # this file
├── agents/openai.yaml   # optional Codex/ChatGPT presentation metadata
├── references/           # detailed, single-topic reference docs
├── examples/             # verified, runnable example walkthroughs
└── evals/                # deterministic evaluation cases (YAML)
```

## Development and validation

This skill is grounded in the `refresh-engine` source tree in this same
repository (`src/refresh_engine/`, `tests/`, `examples/`, `docs/`). When the
package changes:

1. Re-run `python -c "import refresh_engine; print(sorted(refresh_engine.__all__))"`
   and diff against [references/api-reference.md](references/api-reference.md).
2. Re-run every script referenced under `examples/` (the real `.py` files in
   the main package's `examples/` directory) and update printed output blocks
   if they changed.
3. Run the repository's validation script:
   ```bash
   uv run python scripts/validate_refresh_engine_skill.py
   ```
   which checks SKILL.md frontmatter, that every referenced file exists, that
   every backtick-quoted `refresh_engine` symbol exists in the installed
   package, and that plugin manifests parse as valid JSON.
4. Update [`../CHANGELOG.md`](../CHANGELOG.md).

See [source-of-truth precedence](SKILL.md#source-of-truth) in `SKILL.md`: when
documentation and implementation disagree, trust the installed package and
tests, then document the discrepancy — never invent behavior to fill a gap.
