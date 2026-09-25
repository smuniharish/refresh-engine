# refresh-engine Agent Skill validation

This directory documents the repeatable validation process for the canonical
skill. It is intentionally not a second runtime test suite and does not
provide a host-specific package.

The Agent Skills specification was checked at
[agentskills.io/specification](https://agentskills.io/specification). It
defines `name` and `description` as the required frontmatter. No official
validator is specified there, so validation combines structural checks with
source-backed content review.

## Structural validation

For every change:

1. Confirm [`../skills/refresh-engine/SKILL.md`](../skills/refresh-engine/SKILL.md)
   exists and starts with YAML frontmatter.
2. Confirm `name` is exactly `refresh-engine` (the directory name), contains
   only lowercase letters and hyphens, and is at most 64 characters.
3. Confirm `description` is non-empty, at most 1024 characters, and states
   both the capability and when to activate it.
4. Confirm only `name` and `description` appear in frontmatter unless the
   current specification and a demonstrated host requirement justify more.
5. Resolve every relative Markdown target in the skill, its references, and
   this directory; no target may point at a deleted file.
6. Confirm the distribution contains one `skills/refresh-engine/` canonical
   knowledge source and no Claude/Codex/Copilot duplicate.
7. Search the distribution for stale package names (e.g. leftover
   `refresh_engine_skill`/`refresh-engine-agent-skill-v2` naming from earlier
   drafts), invented CLI commands, credentials, and unrelated projects.

## Source-accuracy review

Review every code snippet and factual claim against its source:

| Claim area | Source of truth |
| --- | --- |
| Public import and package version | [`src/refresh_engine/__init__.py`](https://github.com/smuniharish/refresh-engine/blob/main/src/refresh_engine/__init__.py) |
| Constructor signature and lifecycle | [`src/refresh_engine/api/engine.py`](https://github.com/smuniharish/refresh-engine/blob/main/src/refresh_engine/api/engine.py) |
| Dependencies and supported Python floor | [`pyproject.toml`](https://github.com/smuniharish/refresh-engine/blob/main/pyproject.toml) |
| Public API and configuration | [API reference - refresh-engine](https://refresh-engine.readthedocs.io/en/latest/api/) |
| Architecture and lifecycle | [`docs/architecture/overview.md`](https://github.com/smuniharish/refresh-engine/blob/main/docs/architecture/overview.md), [`docs/architecture/lifecycle.md`](https://github.com/smuniharish/refresh-engine/blob/main/docs/architecture/lifecycle.md), [`docs/architecture/decisions.md`](https://github.com/smuniharish/refresh-engine/blob/main/docs/architecture/decisions.md) |
| Refresh modes, fingerprinting, scheduling, state guides | [`docs/guides/`](https://github.com/smuniharish/refresh-engine/tree/main/docs/guides) |
| Executable workflows | [`examples/`](https://github.com/smuniharish/refresh-engine/tree/main/examples) and [`tests/`](https://github.com/smuniharish/refresh-engine/tree/main/tests) |

If a behavior lacks an implementation, test, or authoritative document, omit
it from the skill rather than infer an API.

## Agent-task matrix

The following matrix was reviewed against the canonical
[`SKILL.md`](../skills/refresh-engine/SKILL.md), its references, the runtime
implementation, and the linked examples/tests.

| Task | Activates | Grounded route | Avoids |
| --- | --- | --- | --- |
| "I need to refresh a collection of resources periodically. How should I integrate refresh-engine?" | Yes | [`references/integration.md`](../skills/refresh-engine/references/integration.md) Pattern A/C, real `ResourceSource`/`RefreshOperation` contracts. | Inventing a constructor signature or a CLI. |
| "I have 10,000 resources and only a few change between refreshes. How can I avoid refreshing everything?" | Yes | [`references/incremental-refresh.md`](../skills/refresh-engine/references/incremental-refresh.md) and [`references/fingerprinting.md`](../skills/refresh-engine/references/fingerprinting.md); verified `test_ten_thousand_unchanged_resources_are_skipped`. | Claiming an unverified performance guarantee, or reprocessing everything "to be safe". |
| "I need refresh every hour but cannot block my application's main execution." | Yes | [`references/scheduling.md`](../skills/refresh-engine/references/scheduling.md) → `AsyncScheduler`/`ExternalSchedulerAdapter`, actual start/pause/resume/stop lifecycle. | A manual thread or blocking `while True` loop. |
| "Resource A depends on B and B changes. How should refresh-engine handle this?" | Yes | [`references/dependency-refresh.md`](../skills/refresh-engine/references/dependency-refresh.md) → `DependencyGraph`/`ImpactAnalyzer`/topological `RefreshPlan.levels`. | Manually ordering refresh calls in application code. |
| "Discovery failed temporarily. Should I assume every existing resource was deleted?" | Yes | [`references/state-management.md`](../skills/refresh-engine/references/state-management.md) → `DiscoveryError` handling, `complete=False` never implies deletion. | Destructive deletion inferred from a transient failure. |
| "Should I build an MCP-specific refresh engine on top of refresh-engine?" | Yes | `SKILL.md` ownership-boundary statement and Pattern E adapter in [`references/integration.md`](../skills/refresh-engine/references/integration.md). | Domain-coupling `refresh_engine` types, or duplicating its infrastructure. |
| "Add tests for added/modified/deleted/dependency-changed resources." | Yes | [`references/integration.md`](../skills/refresh-engine/references/integration.md) testing section, `tests/helpers.py` fixture pattern. | Mocking instead of the package's own in-memory test doubles. |
| "Why wasn't a deleted resource removed from the destination?" | Yes | [`references/troubleshooting.md`](../skills/refresh-engine/references/troubleshooting.md) → discovery-completeness diagnosis. | Assuming a bug before checking `DiscoveryResult.complete`. |

## Repository validation

Skill-only work should at least run the structural/link/source review above
and review the resulting Git diff. If runtime files change, run the actual
repository CI-equivalent checks (`ruff check .`, `black --check .`,
`pyrefly check`, `pytest`, `python scripts/check_docs_examples.py`,
`python scripts/run_examples.py`, `mkdocs build --strict`) as defined in
[`.github/workflows/ci.yml`](https://github.com/smuniharish/refresh-engine/blob/main/.github/workflows/ci.yml).
This distribution must not require a refresh-engine runtime change.
