# `.agents/plugins/` (informational placeholder)

This directory exists only to mirror the layout originally requested for this
distribution. It intentionally contains no content.

**Verified fact (2026):** neither Codex CLI/ChatGPT nor GitHub Copilot read a
`.agents/plugins/` directory. Both discover skills by scanning `.agents/skills/`
directories (from the current working directory up to the repository root),
per <https://learn.chatgpt.com/docs/build-skills> and
<https://docs.github.com/en/copilot/concepts/agents/about-agent-skills>.

The canonical skill in this repository is discoverable at the repository root
via a checked-in symlink:

```
<repo-root>/.agents/skills/refresh-engine -> refresh-engine-skills/skills/refresh-engine
```

See [`../../README.md`](../../README.md) for the full list of discovery paths
wired up in this repository, and
[`../.codex-plugin/plugin.json`](../.codex-plugin/plugin.json) for
Codex-specific notes.
