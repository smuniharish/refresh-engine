# refresh-engine Agent Skills

This directory distributes two [Agent Skills](https://agentskills.io) for
`refresh-engine` 1.0.0. They provide focused workflows and checked templates;
they do not add runtime code.

| Skill | Use it for |
| --- | --- |
| [`refresh-engine`](skills/refresh-engine/SKILL.md) | Using refresh-engine in an application. |
| [`refresh-engine-extend`](skills/refresh-engine-extend/SKILL.md) | Implementing custom ABC integrations, stores, retry policies, or telemetry. |

Each skill has task routes, on-demand references, sample assets, and activation
evaluations. See [`validation/README.md`](validation/README.md) for checks.
The frontmatter follows the Agent Skills specification, and `skills.sh.json`
registers both skills for discovery by the skills CLI.

## Installation

Install one or both skills with the [skills CLI](https://www.skills.sh/docs/cli):

```bash
npx skills add https://github.com/smuniharish/refresh-engine/tree/master/refresh-engine-skills/skills/refresh-engine
npx skills add https://github.com/smuniharish/refresh-engine/tree/master/refresh-engine-skills/skills/refresh-engine-extend
```

Or copy either complete skill directory into an Agent Skills directory.
