# refresh-engine-skills

Agent Skills / plugin distribution for the [`refresh-engine`](https://pypi.org/project/refresh-engine/)
Python package (import name `refresh_engine`, docs at
<https://refresh-engine.readthedocs.io>).

```
refresh-engine          =  Python runtime library (discovery, fingerprinting,
                            change detection, incremental refresh, dependency
                            analysis, scheduling, concurrency, state, execution)

refresh-engine-skills   =  AI-agent knowledge/integration layer that teaches
                            coding agents how to use that library correctly
```

This directory does **not** reimplement, fork, or vendor `refresh_engine`. It
contains only Markdown/YAML/JSON assets that teach AI coding agents (Claude
Code, Codex CLI/ChatGPT, GitHub Copilot, and any other host implementing the
open [Agent Skills specification](https://agentskills.io/specification)) how
to integrate, extend, debug, and test the real package. Every API mentioned
is verified against the source in [`../src/refresh_engine/`](../src/refresh_engine),
[`../tests/`](../tests), and [`../examples/`](../examples) in this same
repository.

## Canonical skill

There is exactly **one** canonical skill:

```
refresh-engine-skills/skills/refresh-engine/SKILL.md
```

Everything else in this directory is either supporting material for that
skill (`references/`, `examples/`, `evals/`) or host-specific
metadata/registration (`.claude-plugin/`, `.codex-plugin/`, root-level
`plugin.json`) that points back at the same canonical `SKILL.md` — no
vendor-specific copies of the skill content exist.

## Repository layout

```
refresh-engine-skills/
├── skills/
│   └── refresh-engine/
│       ├── SKILL.md              # canonical skill (load this first)
│       ├── README.md             # skill-level documentation
│       ├── agents/openai.yaml    # optional Codex/ChatGPT presentation metadata
│       ├── references/           # 12 focused, single-topic reference docs
│       ├── examples/             # 5 verified, runnable example walkthroughs
│       └── evals/                # 6 deterministic evaluation cases
├── .claude-plugin/
│   └── plugin.json               # Claude Code plugin manifest
├── .codex-plugin/
│   └── plugin.json               # informational only — see file comment
├── .agents/
│   └── plugins/README.md         # informational placeholder — see file
├── plugin.json                   # portable/spec-agnostic manifest
├── README.md                      # this file
└── CHANGELOG.md
```

Two additional paths are registered at the **repository root** (not inside
this directory) so that Codex CLI, GitHub Copilot, and Claude Code discover
the skill with zero install steps, per each host's real discovery mechanism
(researched directly from each host's current documentation — see
"Installation" below):

```
<repo-root>/.agents/skills/refresh-engine   (symlink -> refresh-engine-skills/skills/refresh-engine)
<repo-root>/.claude/skills/refresh-engine   (symlink -> refresh-engine-skills/skills/refresh-engine)
```

These are committed as real Git symlinks (mode `120000`), so no skill content
is duplicated anywhere in the repository.

## Installation

### Zero-install (recommended): clone the repository

Because this repository ships the skill at well-known discovery paths, most
hosts find it automatically once the repository is opened as a project:

| Host | Discovery path used | Notes |
|---|---|---|
| **Codex CLI / ChatGPT** | `.agents/skills/refresh-engine` | Codex scans `.agents/skills` from the CWD up to the repo root and "supports symlinked skill folders and follows the symlink target." No install step needed. |
| **GitHub Copilot** (CLI, agent mode, coding agent, code review) | `.agents/skills/refresh-engine` (or `.github/skills`, `.claude/skills`) | Copilot recognizes project skills at any of `.github/skills`, `.claude/skills`, or `.agents/skills`. |
| **Claude Code** | `.claude/skills/refresh-engine` | Claude Code auto-discovers project skills directly from `.claude/skills/` — no plugin install required. |

**Windows note:** Git symlinks require either Developer Mode or an elevated
`git config core.symlinks true`, neither of which this environment had
available while authoring this distribution. Without that, `git checkout`
materializes `.agents/skills/refresh-engine` and `.claude/skills/refresh-engine`
as plain text files containing the symlink target instead of real symlinks —
they will not be resolved as directories. If you're on Windows and these
paths don't work:

```powershell
git config core.symlinks true
git checkout -- .agents .claude
```

(requires Developer Mode or an elevated shell), or use one of the
alternatives below.

### Claude Code plugin install

The [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) manifest makes
this a valid Claude Code plugin. Install it from a locally cloned copy of
this repository, or add this repository as a plugin marketplace source,
per Claude Code's `/plugin` command documentation. Exact marketplace-install
commands depend on how you host this repository (public GitHub, private
Git remote, etc.) and were not hard-coded here per the rule against
inventing unverified install commands — validate against
`claude plugin --help` in your environment before relying on a specific
invocation.

### Portable install via `npx skills`

```bash
npx skills add <owner>/refresh-engine --skill refresh-engine
```

replacing `<owner>/refresh-engine` with this repository's actual
`owner/repo` (or a full Git URL, or
`.../tree/main/refresh-engine-skills/skills/refresh-engine` to point directly
at the skill subdirectory). Verified against the `skills` CLI's current
documented syntax (`npx skills add <owner/repo> --skill <skill-name>`,
`npx skills add <git-url>`); the exact command was **not executed** against
a live published copy of this repository as part of this work — validate it
once this repository (or a fork) is reachable at the URL you intend to use.

### Manual copy

Any host that reads plain `SKILL.md` + `references/` + `examples/`
directories can use this skill by copying (not moving)
`skills/refresh-engine/` into that host's project-skill directory.

## Versioning

The skill distribution has its own `CHANGELOG.md` and version, currently
`0.1.0`, matching the current `refresh-engine` package release
(`../pyproject.toml`). Skill and package versions are **not guaranteed to be
identical** going forward — the skill version tracks meaningful content
changes to the skill itself (new references, corrected API drift, etc.),
while the package version tracks code changes. When the package ships a
release with public API changes, the skill must be re-audited and its own
version bumped independently; a package patch release with no public API
change does not require a skill version bump.

## Validation

See [`../scripts/validate_refresh_engine_skill.py`](../scripts/validate_refresh_engine_skill.py)
and the `validate-skill` step in [`../.github/workflows/ci.yml`](../.github/workflows/ci.yml)
for the automated checks that keep this distribution from drifting out of
sync with the real package (frontmatter validity, reference/example file
existence, API-symbol drift against `refresh_engine.__all__`, and JSON
manifest validity).
