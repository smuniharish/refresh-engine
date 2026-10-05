# Examples

Runnable, self-contained programs that use only the public `refresh_engine` API.
Each example prints deterministic output, and the expected output is stored in
[`output/`](output/) and verified in continuous integration.

| Example | Demonstrates |
| --- | --- |
| [`01_quickstart.py`](01_quickstart.py) | First, unchanged, and changed incremental refreshes |
| [`02_refresh_modes.py`](02_refresh_modes.py) | Full, targeted (IDs and composed selectors), incremental, and dependency-aware refreshes |
| [`03_dependencies.py`](03_dependencies.py) | Dependency ordering, skipped dependents, automatic retries, deletions, and cycles |
| [`04_fingerprints.py`](04_fingerprints.py) | Reliable cheap indicators, `ContentHash`, and a custom fingerprint strategy |
| [`05_scheduling.py`](05_scheduling.py) | `AsyncScheduler` and refreshes requested from a thread-based scheduler |
| [`06_failure_handling.py`](06_failure_handling.py) | Retries, timeouts, partial discovery, and the fail-fast policy |
| [`07_overlap_and_cancellation.py`](07_overlap_and_cancellation.py) | Every overlap policy, caller cancellation, and shutdown |
| [`08_observability.py`](08_observability.py) | Structured logging with redaction, and metrics |
| [`09_filesystem.py`](09_filesystem.py) | Files as resources, with blocking I/O kept off the event loop |
| [`10_sqlite_state_store.py`](10_sqlite_state_store.py) | Durable state in SQLite across restarts |
| [`11_postgres_state_store.py`](11_postgres_state_store.py) | Durable state in PostgreSQL across restarts |
| [`12_abc_extensions.py`](12_abc_extensions.py) | Extensions built on the abstract base classes |
| [`13_scale.py`](13_scale.py) | Ten thousand resources, of which only the changed ones are read |

[`common.py`](common.py) holds the in-memory source and operation that several
examples share. [`sqlite_store.py`](sqlite_store.py) and
[`postgres_store.py`](postgres_store.py) are reference state stores that an
application can adapt; they are not part of the installed package.

## Running the examples

Install the package, then run an example from this directory:

```console
python 01_quickstart.py
```

Example 11 needs a PostgreSQL server and the `psycopg` package. Point it at a
database whose `refresh_engine_state` table it may create and clear:

```console
export REFRESH_ENGINE_POSTGRES_DSN="postgresql://postgres:postgres@localhost:5432/postgres"
python 11_postgres_state_store.py
```

From the repository root, `python scripts/run_examples.py` runs every example and
compares its output with the expected output.
