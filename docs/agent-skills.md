# Agent Skills

`refresh-engine` publishes one portable [Agent Skill](https://agentskills.io/specification)
that teaches coding agents how to integrate, configure, debug, test, and
extend the existing `refresh_engine` package. The skill is documentation and
procedural guidance; it is not a Python runtime component and does not change
how `refresh-engine` itself is installed.

| Component | Location |
| --- | --- |
| refresh-engine Python runtime | [`src/refresh_engine/`](https://github.com/smuniharish/refresh-engine/tree/main/src/refresh_engine) |
| Canonical Agent Skill | [`refresh-engine-skills/skills/refresh-engine/`](https://github.com/smuniharish/refresh-engine/tree/main/refresh-engine-skills/skills/refresh-engine) |
| Canonical instructions | [`SKILL.md`](https://github.com/smuniharish/refresh-engine/blob/main/refresh-engine-skills/skills/refresh-engine/SKILL.md) |

The skill follows the [Agent Skills specification](https://agentskills.io/specification)
and contains only the required `name` and `description` frontmatter. There is
no separate Claude, Codex, Cursor, or Copilot copy of the skill.

## Install from skills.sh

The [skills CLI](https://www.skills.sh/docs/cli) installs skills from a
GitHub source. Install the refresh-engine skill directory directly:

```
npx skills add https://github.com/smuniharish/refresh-engine/tree/main/refresh-engine-skills/skills/refresh-engine
```

This is the portable installation route. Follow the CLI's current target
selection prompts, then verify that it placed the `refresh-engine` folder in
the target agent's supported skills directory. The shorthand
`npx skills add refresh-engine-skills` is **not** a valid source identifier.

## Install manually

First obtain the canonical skill directory from the
[repository](https://github.com/smuniharish/refresh-engine/tree/main/refresh-engine-skills/skills/refresh-engine).
Copy the complete `refresh-engine` directory, including `SKILL.md` and
`references/`, into one of the host-specific locations below. Do not copy
only `SKILL.md`, because it links to the bundled references.

### Claude Code

Claude Code discovers standalone skills in:

| Scope | Destination |
| --- | --- |
| Current repository | `.claude/skills/refresh-engine/` |
| All local projects | `~/.claude/skills/refresh-engine/` |

Start or restart Claude Code after copying the directory. Claude can select
the skill when its description matches the task, or you can invoke it with
`/refresh-engine`. See [Claude Code Skills](https://code.claude.com/docs/en/skills).

refresh-engine does **not** currently ship a Claude plugin manifest or
marketplace package. Do not use `claude plugin install refresh-engine-skills`.
A plugin should be added only if refresh-engine later needs to distribute
multiple Claude-specific components; the standalone skill is sufficient
today.

### Codex

Codex discovers repository skills by scanning `.agents/skills` from the
working directory to the repository root. Copy the directory to:

| Scope | Destination |
| --- | --- |
| Current repository | `.agents/skills/refresh-engine/` |
| All local projects | `~/.agents/skills/refresh-engine/` |

Codex detects changes automatically; restart it if the skill does not
appear. Invoke it explicitly with `$refresh-engine` or use `/skills` to
inspect available skills. See
[ChatGPT and Codex Skills](https://learn.chatgpt.com/docs/build-skills).

### Cursor

Cursor supports the standard `.agents/skills` locations, which makes the
Codex layout above portable. It also supports Cursor-specific locations:

| Scope | Destination |
| --- | --- |
| Current repository | `.agents/skills/refresh-engine/` or `.cursor/skills/refresh-engine/` |
| All local projects | `~/.agents/skills/refresh-engine/` or `~/.cursor/skills/refresh-engine/` |

Restart Cursor after copying the directory. In Agent chat, type `/` and
select `refresh-engine` to attach it to a message; Cursor can also activate
it from its description. See [Cursor Agent Skills](https://cursor.com/docs/skills).

### GitHub Copilot

GitHub Copilot supports the standard `.agents/skills` layout, and also the
following project and personal locations:

| Scope | Destination |
| --- | --- |
| Current repository | `.agents/skills/refresh-engine/`, `.github/skills/refresh-engine/`, or `.claude/skills/refresh-engine/` |
| All local projects | `~/.agents/skills/refresh-engine/` or `~/.copilot/skills/refresh-engine/` |

For Copilot CLI, start a new session or run `/skills reload`, then verify the
skill with `/skills info refresh-engine`. You can explicitly request it in a
prompt using `/refresh-engine`. See
[Adding agent skills for GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills).

### Other Agent Skills-compatible hosts

Use the host's documented skill directory and copy the complete
`refresh-engine` folder there. The canonical skill relies only on the
standard `SKILL.md` frontmatter and sibling `references/` directory, so it
does not require a host-specific adapter.

If a host needs package metadata, a registry entry, or a plugin manifest,
follow that host's current official documentation and add only a thin
adapter that points to this canonical skill. Do not duplicate the skill
instructions.

## Update and verify

To update a manual installation, replace the complete installed
`refresh-engine` folder with the latest directory from the repository, then
restart or reload the host.

After installation, confirm all of the following:

1. The directory name is `refresh-engine`.
2. `SKILL.md` and `references/` are present in that directory.
3. The host lists `refresh-engine` as an available skill, if it exposes a
   skill listing command or UI.
4. A task about integrating, refreshing, or dependency-tracking resources
   with `refresh_engine` activates or can explicitly invoke the skill.

See the distribution's
[README](https://github.com/smuniharish/refresh-engine/blob/main/refresh-engine-skills/README.md)
and
[validation process](https://github.com/smuniharish/refresh-engine/blob/main/refresh-engine-skills/validation/README.md)
for maintenance details.
