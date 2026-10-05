# Validating the Agent Skill

Validate the skill whenever it, the public API, or documented behavior changes.
Continuous integration runs the automated checks on every pull request.

## Automated checks

Run these from the repository root:

```bash
uvx --from skills-ref==0.1.1 agentskills validate refresh-engine-skills/skills/refresh-engine
uv run python scripts/check_docs.py
uv run ruff check refresh-engine-skills
uv run pyrefly check
```

- `agentskills validate`, from the `skills-ref` package, is the reference validator
  of the [Agent Skills specification](https://agentskills.io/specification). It
  checks the frontmatter, the name, and the directory layout.
- `scripts/check_docs.py` compiles every Python block, runs the blocks marked
  `<!-- test -->` and compares their output, runs the template in `assets/`,
  rejects imports of non-public names, and resolves every link.
- `ruff` and `pyrefly` lint and type-check the template.

## Review checklist

1. `SKILL.md` stays under 500 lines, and its `description` states what the skill
   does and when to use it, in at most 1024 characters.
2. `metadata.version` matches `refresh_engine.__version__`.
3. Every reference is linked from `SKILL.md` and is reachable in one step.
4. Every signature, default, and behavior matches its source of truth:

    | Claim | Source of truth |
    | --- | --- |
    | Public names | `refresh_engine.__all__` in `src/refresh_engine/__init__.py` |
    | Signatures, defaults, and documented behavior | The docstrings in `src/refresh_engine/`, rendered in the [API reference](https://refresh-engine.readthedocs.io/en/latest/api/) |
    | Refresh semantics | The [guides](https://refresh-engine.readthedocs.io/en/latest/guides/) and the tests in `tests/` |
    | Runnable patterns | `examples/` and their expected output in `examples/output/` |

5. The skill describes only public behavior. It never mentions private modules,
   unreleased features, or credentials.

## Activation scenarios

Use these requests to check that an agent selects the skill and follows the right
reference. Each answer must use the public API and avoid the listed mistake.

| Request | Expected route | Must avoid |
| --- | --- | --- |
| "Keep our search index in sync with the articles table." | `SKILL.md` workflow, the template, and `sources-and-operations.md` | Hand-written diffing, hashing, or scheduling. |
| "We have 50,000 documents and only a few change each hour." | `change-detection.md`: cheap indicators and fingerprint choice | Refreshing everything, or unreliable indicators marked reliable. |
| "Refresh every 10 minutes without blocking the web app." | `scheduling.md`: `AsyncScheduler` within the application lifespan | Threads with blocking loops, or `time.sleep`. |
| "Report B depends on table A; rebuild B when A changes." | `dependencies.md`: declared dependencies and `DEPENDENCY_AWARE` | Ordering calls manually, or one refresh per level. |
| "The catalog API timed out; were all our records deleted?" | `failure-handling.md` and `troubleshooting.md`: discovery failures never delete | Returning an empty complete discovery on errors. |
| "Make state survive restarts in PostgreSQL." | `state-stores.md`: `StateStoreABC` and `apply_changes` | Non-atomic writes, or lossy serialization. |
| "Write tests for added, modified, deleted, and failed resources." | `testing.md`: in-memory fakes and result assertions | Mocking engine internals. |
| "Why is every resource refreshed on every run?" | `troubleshooting.md`: volatile fingerprints or lost state | Disabling change detection. |
