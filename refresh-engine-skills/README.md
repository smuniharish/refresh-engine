# refresh-engine Agent Skills

This directory is the canonical Agent Skills distribution for refresh-engine.
It contains procedural guidance for coding agents that need to integrate,
configure, test, or debug the existing `refresh_engine` package.

It is not a Python package and does not add runtime behavior.

| Component | Location | Purpose |
| --- | --- | --- |
| refresh-engine runtime | [`src/refresh_engine/`](https://github.com/smuniharish/refresh-engine/tree/main/src/refresh_engine) | The published Python package and its supported public API. |
| refresh-engine Agent Skill | [`skills/refresh-engine/`](skills/refresh-engine/) | Canonical agent-oriented instructions and concise reference material. |
| Skill validation | [`validation/`](validation/) | Validation procedure and realistic activation/task matrix. |

## Agent Skills format

The canonical skill follows the Agent Skills `SKILL.md` format documented at
[agentskills.io/specification](https://agentskills.io/specification): a
directory-scoped Markdown instruction file with required `name` and
`description` YAML frontmatter. Its `name` matches its containing directory
(`refresh-engine`), and only the specification's required frontmatter is used
for portability.

Compatible agents should load
[`skills/refresh-engine/SKILL.md`](skills/refresh-engine/SKILL.md) when
working on refresh-engine integrations, incremental/dependency-aware refresh,
or background scheduling. The skill links to the repository's authoritative
[documentation](https://refresh-engine.readthedocs.io) and
[examples](https://github.com/smuniharish/refresh-engine/tree/main/examples)
instead of maintaining a second copy of them.

For a host that supports installing skills directly from a Git repository
(for example the portable
[`npx skills add`](https://github.com/vercel-labs/skills) CLI), the verified
command is:

```bash
npx skills add smuniharish/refresh-engine --skill refresh-engine
```

This repository intentionally provides no Claude, Codex, or Copilot
plugin/adapter manifest, because none is required to consume the canonical
`SKILL.md` — those hosts, and any other Agent Skills–compatible host, can load
`skills/refresh-engine/SKILL.md` directly (for example by copying or
symlinking it into the host's own skills directory). Do not assume any other
install command (e.g. a hypothetical `claude plugin install` or
`codex plugin add`) works until the corresponding marketplace/registry
metadata for this repository actually exists.

## Maintaining the distribution

When refresh-engine's public API, supported integrations, or documented
behavior changes:

1. Update the canonical skill and only the reference material affected by
   that verified change.
2. Link to the corresponding implementation, tests, examples, or
   documentation; do not duplicate runtime logic.
3. Run the process in [`validation/README.md`](validation/README.md).
4. Do not add platform-specific copies of the skill text. Add thin metadata
   only when a host's current official documentation demonstrates it is
   required.

The distribution is covered by the repository's license; see the
[repository license](https://github.com/smuniharish/refresh-engine/blob/main/LICENSE).
