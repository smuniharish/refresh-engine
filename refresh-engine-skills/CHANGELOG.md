# Changelog

All notable changes to the `refresh-engine-skills` distribution are documented
in this file. This changelog tracks the skill distribution itself, not the
`refresh-engine` Python package (see [`../CHANGELOG.md`](../CHANGELOG.md) for
that).

The skill distribution version does not necessarily move in lockstep with the
package version — see [README.md § Versioning](README.md#versioning).

## [0.1.0] - Initial release

Grounded against `refresh-engine` `0.1.0`.

### Added

- Canonical skill at `skills/refresh-engine/SKILL.md`, verified against
  `src/refresh_engine/` (all submodules), `tests/`, `examples/`, and `docs/`
  in this repository.
- 12 reference documents: architecture, api-reference, incremental-refresh,
  fingerprinting, dependency-refresh, scheduling, concurrency,
  state-management, failure-handling, testing, integration-patterns,
  troubleshooting.
- 5 example walkthroughs (basic, incremental, scheduled, dependency-aware,
  custom-extension), each reproducing a real script from `../examples/` with
  verified printed output.
- 6 deterministic evaluation cases under `skills/refresh-engine/evals/`
  covering basic integration, incremental refresh, scheduling, dependency
  awareness, failure-handling safety, and architectural domain boundaries.
- Claude Code plugin manifest (`.claude-plugin/plugin.json`).
- Informational Codex plugin metadata (`.codex-plugin/plugin.json`) and
  Codex/ChatGPT presentation metadata
  (`skills/refresh-engine/agents/openai.yaml`).
- Portable root manifest (`plugin.json`).
- Repository-root discovery symlinks: `.agents/skills/refresh-engine` and
  `.claude/skills/refresh-engine`.
- Skill validation script and CI step (`scripts/validate_refresh_engine_skill.py`).

### Known limitations at this release

- The `npx skills add` install path is documented but was not executed
  against a live, published copy of this repository (no network-reachable
  fork existed at authoring time).
- Windows symlink checkout requires `git config core.symlinks true` plus
  Developer Mode or an elevated shell; the authoring environment could not
  enable this, so the fallback behavior (plain text file instead of a real
  directory symlink) is documented but not exercised end-to-end on Windows.
