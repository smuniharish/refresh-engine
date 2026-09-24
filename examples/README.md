# Runnable examples

Run examples from this directory with Python 3.12 after installing the package:

```text
python 01_basic_and_modes.py
```

The examples are domain-neutral and cover all requested integration categories:

| Category | Example |
| --- | --- |
| basic and incremental refresh | `01_basic_and_modes.py` |
| full refresh | `01_basic_and_modes.py` |
| targeted refresh and selectors | `01_basic_and_modes.py`, `03_triggers_and_selectors.py` |
| dependency-aware refresh | `02_dependencies_and_fingerprints.py` |
| fingerprint strategies | `02_dependencies_and_fingerprints.py` |
| event and query triggers | `03_triggers_and_selectors.py` |
| asyncio scheduling | `04_async_and_external_scheduling.py` |
| external scheduling | `04_async_and_external_scheduling.py` |
| retry, timeout, and failure policy | `06_resilience_and_partial_discovery.py` |
| partial-discovery/deletion safety | `06_resilience_and_partial_discovery.py` |
| events, logging, redaction, and metrics | `07_events_and_observability.py` |
| custom state stores and overlap policy | `08_custom_store_and_overlap.py` |
| filesystem resources | `09_filesystem_resources.py` |
| 10,000 resources and generic consumer types | `10_scale_and_genericity.py` |
| cancellation and graceful shutdown | `11_cancellation_and_shutdown.py` |
| durable SQLite state across restarts | `12_sqlite_state_store.py` |
| inheritance-based ABC extensions | `13_abc_extensions.py` |
| durable PostgreSQL state, restart continuity, and rollback | `14_postgres_state_store.py` |

Each example owns and shuts down its scheduler/engine. They use only the public
package API, plus documented observability helpers.

The SQLite and PostgreSQL store implementations are consumer-owned reference adapters,
not production package exports. The PostgreSQL smoke example requires psycopg from the
development dependency group and a `REFRESH_ENGINE_POSTGRES_DSN` environment variable.
