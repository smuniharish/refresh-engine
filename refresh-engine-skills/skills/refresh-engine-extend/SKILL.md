---
name: refresh-engine-extend
description: >-
  Use this skill when implementing or testing custom refresh-engine ABC
  integrations, a StateStore or StateTransaction, ResourceSelector,
  MetricsSink, event handling or publishing, a tenacity retry policy, or
  OpenTelemetry integration. Also use it for StateStoreContract work. Use
  refresh-engine for ordinary application integration and async submission.
license: Apache-2.0
compatibility: >-
  Python 3.12 or newer with refresh-engine 1.0.0 installed. OpenTelemetry
  integration is optional through refresh-engine[otel].
metadata:
  version: "1.0.0"
  documentation: "https://refresh-engine.readthedocs.io/"
---

# Extending refresh-engine

This skill covers the extension surface planned for refresh-engine 1.0.0. The
API and code may still be settling; use the installed package's public
signatures as the authority.

## Task routes

| Task | Read |
| --- | --- |
| Choose an extension point | [Extension points](./references/extension-points.md) |
| Implement a persistent state store | [State-store contract](./references/state-store-contract.md) |
| Configure retries or telemetry | [Retries and telemetry](./references/retries-and-telemetry.md) |

## Extension workflow

1. Find the ABC that corresponds to the integration. Implement its public
   abstract members from the installed package.
2. Use a plain async function for an operation or event handler when that is
   sufficient. Other extension objects subclass their ABC.
3. Await `StateStore.transaction()`; implement `MetricsSink` writes and event
   handlers asynchronously.
4. For a state backend, use `refresh_engine.testing.StateStoreContract` as the
   reusable contract.
5. Run the relevant project tests and validate the sample syntax with
   `python scripts/check_skills.py`.

## Templates and references

- [ABC integration template](./assets/extension_template.py): extension-point
  checklist without assumed method signatures.
- [Store contract test template](./assets/store_contract_test.py): starting
  point for a `StateStoreContract` test.
- [Extension-point guide](./references/extension-points.md)
- [State-store contract guide](./references/state-store-contract.md)
- [Retry and telemetry guide](./references/retries-and-telemetry.md)

`AsyncEventPublisher` is the queued concrete publisher. Optional telemetry is
`refresh-engine[otel]` and
`refresh_engine.otel.OpenTelemetryEventHandler`.
