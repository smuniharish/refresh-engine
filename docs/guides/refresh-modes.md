# Refresh modes

| Mode | Selection |
| --- | --- |
| `INCREMENTAL` | added, modified, and safely inferred deleted resources |
| `FULL` | all discovered resources, plus safe deletions |
| `TARGETED` | explicit `resource_ids`; at least one ID is required |
| `DEPENDENCY_AWARE` | direct changes and their transitive dependents |

```python
from refresh_engine import RefreshMode

full = await engine.refresh(mode=RefreshMode.FULL)
targeted = await engine.refresh(
    mode=RefreshMode.TARGETED,
    resource_ids={"resource-a", "resource-b"},
)
dependency_aware = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)

print(full.refreshed_count)
print(targeted.refreshed_count)
print(dependency_aware.impacted_count)
```

The returned `RefreshResult` contains counts, per-resource execution results, errors,
warnings, timings, and the refresh ID. Exact counts depend on source state. In the
[refresh mode example](examples-and-results.md#incremental-full-and-targeted-refresh),
the output shows two resources in the full refresh, one targeted resource, and one
modified resource in the incremental refresh.

`IDSelector`, `TagSelector`, and `PredicateSelector` are reusable matching utilities
for application trigger mapping and discovery filtering. `RefreshEngine.refresh()`
accepts IDs directly rather than a selector object.
