# refresh-engine Agent Skill

This directory distributes the [Agent Skill](https://agentskills.io) for
refresh-engine. The skill teaches AI coding agents to integrate, configure, test,
and troubleshoot the `refresh_engine` package. It contains instructions and a
template only; it adds no runtime code.

| Path | Contents |
| --- | --- |
| [`skills/refresh-engine/SKILL.md`](skills/refresh-engine/SKILL.md) | Workflow, rules, a verified minimal integration, and the API quick reference. |
| [`skills/refresh-engine/references/`](skills/refresh-engine/references/) | Focused guides that agents read on demand. |
| [`skills/refresh-engine/assets/`](skills/refresh-engine/assets/) | A runnable, typed integration template. |
| [`validation/`](validation/README.md) | How the skill is validated. |

The skill follows the [specification](https://agentskills.io/specification): its
`name` matches its directory, and its frontmatter uses only standard fields. It
works unchanged with Claude Code, Codex, Cursor, GitHub Copilot, and other
compatible agents.

## Installation

Install it with the [skills CLI](https://www.skills.sh/docs/cli):

```bash
npx skills add https://github.com/smuniharish/refresh-engine/tree/master/refresh-engine-skills/skills/refresh-engine
```

Or copy the complete `skills/refresh-engine` directory into a skills directory of
your agent. The
[installation guide](https://refresh-engine.readthedocs.io/en/latest/agent-skills/)
lists the directories each agent reads.

## Maintenance

The skill is versioned with the package; `metadata.version` in `SKILL.md` matches
the release it describes. When the public API or documented behavior changes,
update the affected skill files in the same change and follow the
[validation guide](validation/README.md).
