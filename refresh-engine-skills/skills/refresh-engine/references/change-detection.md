# Change detection

## How a change is classified

For every observed resource, the engine compares the new fingerprint and
dependencies with the stored state:

| Change | Condition | Refreshed by |
| --- | --- | --- |
| Added | No stored state. | Every mode. |
| Modified | The fingerprint differs, including a fingerprint from another strategy. | Every mode. |
| Dependency changed | Same fingerprint, different dependencies. | Every mode. |
| Unchanged | Same fingerprint and dependencies. | `FULL`, `TARGETED`, and impacted resources in `DEPENDENCY_AWARE`. |
| Deleted | Stored state, but missing from a complete discovery. | Every mode except `TARGETED`, which deletes only targeted IDs. |

A resource whose snapshot or fingerprint fails is not classified: it keeps its
state, is neither refreshed nor deleted, and is reported in `result.errors`.

## Fingerprint strategies

| Strategy | Fingerprints | Use when |
| --- | --- | --- |
| `CompositeHash` (default) | `content`, `metadata`, `version`, `version_token`, and `etag` | The operation uses several snapshot fields. |
| `ContentHash` | `content` | The operation ignores snapshot metadata. |
| `MetadataHash` | `metadata` | Only metadata matters. |
| `VersionTokenFingerprint` | `version_token` as is | The source has a revision number that changes on every edit. |
| `ETagFingerprint` | `etag` as is | The source has strong ETags. |

```python
from refresh_engine import ContentHash, RefreshEngine

engine = RefreshEngine(source, operation, fingerprint=ContentHash())
```

`VersionTokenFingerprint` and `ETagFingerprint` fail a resource whose snapshot has
no token. Dependencies are never part of a fingerprint. Switching strategies makes
the next refresh report every resource as modified once.

## Canonical hashing

The hash strategies use `canonical_digest(value)`, a SHA-256 digest of a canonical,
type-tagged encoding:

- mapping order and set order are ignored;
- `list` and `tuple` are equivalent, and so are `bytes`, `bytearray`, and
  `memoryview`;
- types matter, so `1`, `1.0`, `True`, and `"1"` all differ;
- the digest is identical across processes, platforms, and Python versions.

Supported values are `None`, `bool`, `int`, finite `float`, `str`, binary types,
finite `Decimal`, `UUID`, `datetime`, `date`, `time`, `timedelta`, pure paths,
enumerations, dataclass instances, and mappings, sets, and sequences of these.
Other types, NaN, infinities, and reference cycles raise `FingerprintError`, which
fails only that resource. Convert other objects, such as Pydantic models, to
supported values first, for example with `model.model_dump()`.

## Writing a strategy

Hash exactly what the operation uses:

```python
from refresh_engine import (
    FingerprintStrategyABC,
    ResourceFingerprint,
    ResourceSnapshot,
    canonical_digest,
)


class PublishedFields(FingerprintStrategyABC):
    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        article = snapshot.content
        fields = {"title": article["title"], "body": article["body"]}
        return ResourceFingerprint("sha256:published", canonical_digest(fields))
```

- Equal snapshots must always produce equal fingerprints. Never include clocks,
  random values, object IDs, or values that change without a real change, such as
  view counts.
- Use a distinctive algorithm name, and change it whenever the strategy changes
  what it hashes, so stored fingerprints never match by accident.

## Cheap indicators

A cheap indicator lets the engine skip reading unchanged resources. Discovery
attaches an inexpensive change signal, such as an ETag, a revision number, or a
size and modification time:

```python
Resource(item.id, cheap_indicator=item.etag, cheap_indicator_reliable=True)
```

- When `cheap_indicator_reliable=True` and the indicator equals the stored one,
  `INCREMENTAL` and `DEPENDENCY_AWARE` refreshes reuse the stored state and do not
  call `snapshot()`.
- With `cheap_indicator_reliable=False`, the indicator is only stored; the snapshot
  is always read.
- `FULL` and `TARGETED` refreshes always read snapshots.
- Mark an indicator reliable only if it changes whenever anything the fingerprint
  covers changes. Otherwise changes are missed until the indicator moves. A
  modification time with coarse resolution, or an ETag computed from part of the
  data, is not reliable.

Cheap matches count as unchanged. The `SNAPSHOT_COMPLETED` event reports how many
resources were matched cheaply in its `cheap_matches` attribute.
