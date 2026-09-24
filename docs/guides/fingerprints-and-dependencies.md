# Fingerprints and dependencies

The default `CompositeHash` fingerprints content, metadata, version, version token,
ETag, and dependencies. Alternatives are `ContentHash`, `MetadataHash`,
`VersionTokenFingerprint`, and `ETagFingerprint`. Fingerprinted values must use the
supported deterministic data types; do not include clocks, random values, open
handles, or domain objects without a stable representation.

```python
from refresh_engine import ContentHash, RefreshEngine, RefreshMode

engine = RefreshEngine(source, operation, fingerprint=ContentHash())
result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
print(result.modified_count, result.impacted_count)
```

Dependencies are resource IDs on `Resource` and `ResourceSnapshot`. In
dependency-aware mode, a changed dependency impacts transitive dependents. The
planner executes dependency levels before dependents and rejects cycles. Missing
dependency IDs are external leaves; applications should decide whether that is valid
for their model.

With `derived` depending on a modified `base`, dependency-aware refresh reports:

```text
dependency-aware: modified=1, impacted=1, refreshed=['base', 'derived']
```

See [Architecture](../architecture/overview.md) and
[Architecture decisions](../architecture/decisions.md).
