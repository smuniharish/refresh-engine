---
name: refresh-engine
description: >-
  Use this skill when using refresh-engine in a Python application: keeping a
  search index, cache, embedding collection, or other derived data in sync;
  submitting async refresh work; choosing a retry policy; or handling refresh
  events and optional OpenTelemetry. Use refresh-engine-extend for custom ABC
  integrations or stores, custom telemetry, or StateStoreContract work.
license: Apache-2.0
compatibility: >-
  Python 3.12 or newer. Install refresh-engine 1.0.0 from PyPI. The optional
  OpenTelemetry handler is available from the refresh-engine[otel] extra.
metadata:
  version: "1.0.0"
  documentation: "https://refresh-engine.readthedocs.io/"
---

# Using refresh-engine

Use refresh-engine to keep derived data current from an asynchronous
application. The API and implementation may still be settling; use the
installed package's public API as the authority for exact signatures.

## Task routes

| Task | Read |
| --- | --- |
| Connect an application to refresh-engine | [Application workflow](./references/application-workflow.md) |
| Submit work directly from async code | [Async submission](./references/async-submission.md) |
| Configure the full tenacity retry policy | [Retries](./references/retries.md) |
| Handle lifecycle events or use OpenTelemetry | [Events and telemetry](./references/events-and-telemetry.md) |

## Workflow

1. Install `refresh-engine` and identify the resources whose derived state must
   be refreshed.
2. Implement the required extension objects using the ABCs in the installed
   public API. A plain async operation function is accepted; other extension
   objects subclass their corresponding ABC.
3. Create the engine using the signatures exposed by the installed version.
   Avoid copying method signatures from an unrelated release or example.
4. From async application code, await `engine.submit(...)` directly. Do not
   build a thread-based scheduler bridge around submission.
5. If customization is needed, follow the applicable task route above. Re-run
   the application's tests after adapting the integration.

## Scope boundaries

- Configure tenacity through the complete user policy:
  `RetryConfig(retrying=AsyncRetrying(...))`.
- `refresh-engine[otel]` is optional. Its handler is
  `refresh_engine.otel.OpenTelemetryEventHandler`.
- Event handlers are async. `AsyncEventPublisher` is the queued concrete
  publisher.
- For custom ABC implementations, storage contracts, and telemetry adapters,
  use [refresh-engine-extend](../refresh-engine-extend/SKILL.md).

## Sample

Use [the integration template](./assets/integration_template.py) as a checklist
for selecting extension points. It deliberately leaves abstract method
signatures to the installed package because the API may still be settling.
