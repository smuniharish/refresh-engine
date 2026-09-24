# Contributing

## Setup

Use Python 3.12 and `uv`:

```text
uv sync --frozen
```

Runtime code lives in `src/refresh_engine`; tests live in `tests`.

## Quality checks

Run the smallest relevant test first, then before submitting run:

```text
uv run ruff check .
uv run black --check .
uv run pyrefly check
uv run pytest --cov=refresh_engine
uv run python scripts/check_docs_examples.py
uv run python scripts/run_examples.py
uv run mkdocs build --strict
uv build
uv publish --dry-run --trusted-publishing never dist/*
uv run python scripts/validate_distribution.py dist
```

Environment-tagged benchmark reports in `benchmarks/results/` are accepted when the
environment and raw command are recorded. Do not include generated sites, virtual
environments, local databases, or credentials.

## Changes

- Add tests for behavior changes and preserve deletion/state safety invariants.
- Keep public APIs domain-neutral and typed.
- Update user guides, API documentation, examples, architecture decisions, and
  `CHANGELOG.md` when behavior changes.
- Keep architecture rationale in the consolidated architecture decisions document.
- Report benchmark environment and raw commands.

By contributing, you agree that your contribution is licensed under Apache-2.0.
