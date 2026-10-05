# Contributing

Thank you for helping to improve refresh-engine. This guide explains how to set up
a development environment, which checks a change must pass, and how to keep the
documentation, examples, and Agent Skill accurate.

## Development setup

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/smuniharish/refresh-engine.git
cd refresh-engine
uv sync
```

`uv sync` creates a virtual environment, installs the package in editable mode, and
installs the `dev` dependency group, which combines the `test`, `lint`, and `docs`
groups with release tooling.

## Project layout

| Path | Contents |
| --- | --- |
| `src/refresh_engine/` | The package. Every public name is exported from `refresh_engine`; implementation details live in the private `_internal` package. |
| `tests/unit/`, `tests/integration/` | Tests of each module and of complete refreshes. |
| `tests/property/` | Property-based and stateful tests written with Hypothesis. |
| `examples/` | Runnable examples, with their expected output in `examples/output/`. |
| `docs/` | The documentation site, built with MkDocs. |
| `benchmarks/` | The benchmark script and recorded results. |
| `scripts/` | Tooling for examples, documentation, diagrams, and distributions. |
| `refresh-engine-skills/` | The Agent Skill and its validation guide. |

## Checks

Continuous integration runs the following checks. Run them before opening a pull
request:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyrefly check
uv run pytest --cov
uv run python scripts/run_examples.py
uv run python scripts/check_docs.py
uv run mkdocs build --strict
```

- Tests treat warnings as errors, and coverage must stay at 100% of lines and
  branches. Exclude code from coverage only when it cannot run, such as the body
  of a protocol method.
- Continuous integration runs the Hypothesis tests with more examples. Use the
  same profile locally with `HYPOTHESIS_PROFILE=ci`.
- `examples/11_postgres_state_store.py` runs only when
  `REFRESH_ENGINE_POSTGRES_DSN` is set, for example to
  `postgresql://postgres:postgres@localhost:5432/postgres`.
- After changing an example, regenerate its expected output with
  `uv run python scripts/run_examples.py --update` and review the difference.
- Code blocks in Markdown must compile. Mark a block with `<!-- test -->` to run
  it; a following `text` block holds its exact output.
- `ruff format` also formats the Python blocks in Markdown files. Include example
  files in documentation with the block form of snippets, which ruff leaves
  untouched: a `--8<--` line, the file path, and another `--8<--` line.

Continuous integration also tests Python 3.12, 3.13, and 3.14 on Linux and
Windows, and validates the built distributions:

```bash
uv build
uv run twine check --strict dist/*
uv run python scripts/validate_distribution.py dist --python 3.12 --python 3.14
```

## Making changes

- Add a test for every behavior change, and a regression test for every fix.
- Keep the public API domain-neutral, typed, and documented with Google-style
  docstrings. New public names must be exported from `refresh_engine` and listed
  in the API reference.
- Preserve the guarantees described in the
  [architecture overview](docs/architecture/overview.md), in particular that
  incomplete discoveries never delete anything and that state advances only for
  successful operations.
- Update the guides, examples, Agent Skill, and `CHANGELOG.md` together with the
  behavior they describe.
- When a change affects performance, run the benchmarks before and after it on
  the same machine and include both results.

## Diagrams

Diagrams are written in Mermaid in `docs/assets/diagrams/*.mmd` and committed as
light and dark PNG images. After editing a diagram, render the images with
[Node.js](https://nodejs.org) installed:

```bash
uv run python scripts/render_diagrams.py
```

## Agent Skill

The skill in `refresh-engine-skills/skills/refresh-engine/` must describe the
current public API. After changing the API or the skill, follow
[the skill validation guide](refresh-engine-skills/validation/README.md).

## Releases

1. Update `__version__` in `src/refresh_engine/__init__.py`, the skill's
   `metadata.version`, and `CHANGELOG.md`.
2. Merge the changes into `master` after continuous integration passes.
3. Push a `vX.Y.Z` tag. The release workflow checks that the tag matches the
   package version, runs the checks, and publishes to PyPI with trusted
   publishing and attestations.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE).
