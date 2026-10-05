# Examples

These programs use only the public API and print deterministic output, which is
verified in continuous integration. Find them, with instructions for running them,
in the [`examples`](https://github.com/smuniharish/refresh-engine/tree/HEAD/examples)
directory of the repository. Most of them share this small module:

??? example "`common.py`: an in-memory source and a recording operation"

    ```python
    --8<--
    examples/common.py
    --8<--
    ```

## Quick start

=== "Program"

    ```python
    --8<--
    examples/01_quickstart.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/01_quickstart.txt
    --8<--
    ```

## Refresh modes

=== "Program"

    ```python
    --8<--
    examples/02_refresh_modes.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/02_refresh_modes.txt
    --8<--
    ```

## Dependencies

=== "Program"

    ```python
    --8<--
    examples/03_dependencies.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/03_dependencies.txt
    --8<--
    ```

## Fingerprints and cheap indicators

=== "Program"

    ```python
    --8<--
    examples/04_fingerprints.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/04_fingerprints.txt
    --8<--
    ```

## Scheduling

=== "Program"

    ```python
    --8<--
    examples/05_scheduling.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/05_scheduling.txt
    --8<--
    ```

## Failure handling

=== "Program"

    ```python
    --8<--
    examples/06_failure_handling.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/06_failure_handling.txt
    --8<--
    ```

## Overlap and cancellation

=== "Program"

    ```python
    --8<--
    examples/07_overlap_and_cancellation.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/07_overlap_and_cancellation.txt
    --8<--
    ```

## Observability

=== "Program"

    ```python
    --8<--
    examples/08_observability.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/08_observability.txt
    --8<--
    ```

## Files as resources

=== "Program"

    ```python
    --8<--
    examples/09_filesystem.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/09_filesystem.txt
    --8<--
    ```

## Durable state with SQLite

=== "Program"

    ```python
    --8<--
    examples/10_sqlite_state_store.py
    --8<--
    ```

=== "Store"

    ```python
    --8<--
    examples/sqlite_store.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/10_sqlite_state_store.txt
    --8<--
    ```

## Durable state with PostgreSQL

=== "Program"

    ```python
    --8<--
    examples/11_postgres_state_store.py
    --8<--
    ```

=== "Store"

    ```python
    --8<--
    examples/postgres_store.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/11_postgres_state_store.txt
    --8<--
    ```

## Abstract base classes

=== "Program"

    ```python
    --8<--
    examples/12_abc_extensions.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/12_abc_extensions.txt
    --8<--
    ```

## Scale

=== "Program"

    ```python
    --8<--
    examples/13_scale.py
    --8<--
    ```

=== "Output"

    ```text
    --8<--
    examples/output/13_scale.txt
    --8<--
    ```
