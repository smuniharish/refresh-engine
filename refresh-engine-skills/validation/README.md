# Validating the Agent Skills

Validate both skills whenever they or the documented API change. Run the
repository-local checks from the repository root:

```bash
uv run python scripts/check_skills.py
uvx --from skills-ref agentskills validate refresh-engine-skills/skills/refresh-engine
uvx --from skills-ref agentskills validate refresh-engine-skills/skills/refresh-engine-extend
```

`check_skills.py` checks the distribution manifest, names and metadata,
in-skill links, sample Python syntax, evaluation fixtures, and balanced trigger
queries. `agentskills validate` is the optional reference validator for the
[Agent Skills specification](https://agentskills.io/specification).

## Review checklist

1. Both skills declare version `1.0.0`; keep it aligned with the package
   version they describe.
2. Keep names equal to their directory names and descriptions specific enough
   to route usage questions separately from extension work.
3. Check every API claim against the current public implementation. The API
   and code may still be settling; these skills intentionally state only the
   surface listed in their plan.
4. Keep each sample syntactically valid and ensure every linked reference or
   asset exists.
5. Keep positive and near-miss activation queries balanced in training and
   validation splits. Revisit the boundaries so use cases route to the usage
   skill and custom ABC/store/retry/telemetry work routes to the extension
   skill.

## Evaluations

Each `evals/evals.json` gives task prompts, input files, expected outcomes, and
checkable assertions. `evals/trigger_queries.json` contains positive and
near-miss requests split between training and validation.

To evaluate a change, run each task in a fresh workspace with and without the
skill, then grade its assertions against the expected result. The sample input
files and templates are deliberately narrow: they do not imply additional
runtime behavior or stable method signatures.
