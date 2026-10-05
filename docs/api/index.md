# API reference

Every supported name is importable from the `refresh_engine` package. Modules whose
names start with an underscore are internal and may change without notice.

```python
from refresh_engine import RefreshEngine, RefreshMode, Resource, ResourceSnapshot
```

| Page | Contents |
| --- | --- |
| [Engine](engine.md) | `RefreshEngine` |
| [Models and configuration](models.md) | Resources, snapshots, state, requests, results, configuration, and enumerations |
| [Extension contracts](contracts.md) | Protocols and abstract base classes for sources, operations, fingerprints, state, selectors, and metrics |
| [Fingerprints](fingerprints.md) | Built-in fingerprint strategies and `canonical_digest` |
| [Selectors](selectors.md) | `IDSelector`, `TagSelector`, and `PredicateSelector` |
| [Events](events.md) | Lifecycle events, event handlers, and publishers |
| [Logging and metrics](observability.md) | structlog and metrics handlers, metric sinks, and redaction |
| [Scheduling](scheduling.md) | `AsyncScheduler` and `ExternalSchedulerAdapter` |
| [State stores](stores.md) | `InMemoryStateStore` |
| [Exceptions](errors.md) | The exception hierarchy |

The package follows [semantic versioning](https://semver.org). The names listed in
this reference form the public API.
