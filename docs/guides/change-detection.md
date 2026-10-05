# Change detection

The engine decides whether a resource changed by comparing its **fingerprint** with
the stored one, and avoids reading the resource at all when a **cheap indicator**
proves nothing changed.

## Fingerprint strategies

| Strategy | Fingerprints | Algorithm name |
| --- | --- | --- |
| [`CompositeHash`][refresh_engine.CompositeHash] (default) | content, metadata, version, version token, and ETag | `sha256:composite` |
| [`ContentHash`][refresh_engine.ContentHash] | content only | `sha256:content` |
| [`MetadataHash`][refresh_engine.MetadataHash] | metadata only | `sha256:metadata` |
| [`VersionTokenFingerprint`][refresh_engine.VersionTokenFingerprint] | the version token itself | `version-token` |
| [`ETagFingerprint`][refresh_engine.ETagFingerprint] | the ETag itself | `etag` |

Choose the narrowest strategy that still covers everything your operation depends
on. If the operation reads `snapshot.metadata`, `ContentHash` would miss metadata
changes; if the operation ignores volatile metadata such as view counts, a narrower
strategy avoids needless refreshes.

Dependencies are never part of a fingerprint. The engine compares them separately
and reports a dependency-only change as `DEPENDENCY_CHANGED`.

```python
from refresh_engine import ContentHash, RefreshEngine

engine = RefreshEngine(source, operation, fingerprint=ContentHash())
```

!!! note "Changing the strategy"
    Fingerprints from different strategies never match, so switching strategies, or
    upgrading from a release with a different encoding, makes the next refresh
    report every resource as modified once.

## Canonical hashing

The built-in hash strategies use [`canonical_digest`][refresh_engine.canonical_digest],
which encodes a value into a canonical, type-tagged byte string and returns its
SHA-256 digest. Two values have the same digest exactly when they have the same
types and contents:

- mapping insertion order and set iteration order do not matter;
- `list` and `tuple` are interchangeable, as are `bytes`, `bytearray`, and
  `memoryview`;
- types are part of the encoding, so `1`, `1.0`, `True`, and `"1"` differ;
- the result is the same in every process, on every platform, and on every
  supported Python version.

Supported values are `None`, `bool`, `int`, finite `float`, `str`, binary types,
finite `Decimal`, `UUID`, dates, times, `timedelta`, pure paths, enumerations,
dataclass instances, and mappings, sets, and sequences of supported values. Anything
else, NaN, infinities, and self-referencing structures raise
[`FingerprintError`][refresh_engine.FingerprintError], which fails only the affected
resource.

### Writing a strategy

Implement [`FingerprintStrategy`][refresh_engine.FingerprintStrategy], or subclass
[`FingerprintStrategyABC`][refresh_engine.FingerprintStrategyABC], and hash exactly
the values that matter:

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
        published = {"title": article["title"], "body": article["body"]}
        return ResourceFingerprint("sha256:published", canonical_digest(published))
```

Equal snapshots must always produce equal fingerprints. Never include clocks,
random values, or object identities.

## Cheap indicators

Reading and hashing every resource on every refresh is wasteful when the source
already knows what changed. Discovery can attach a **cheap indicator**, such as an
ETag, a revision number, or a size and modification time, to each resource:

```python
Resource(
    item.id,
    cheap_indicator=item.etag,
    cheap_indicator_reliable=True,
)
```

When `cheap_indicator_reliable` is `True` and the indicator equals the stored one,
the engine reuses the stored fingerprint and does not call `snapshot()`. Otherwise
it reads the snapshot and compares fingerprints as usual.

!!! danger "Only mark indicators reliable when equality proves equality"
    A reliable indicator must change whenever anything the fingerprint covers
    changes. An indicator that can stay the same across a change hides that change
    until the indicator moves.

Cheap indicators are not used by `FULL` and `TARGETED` refreshes, which always read
fresh snapshots. When a dependency-aware refresh needs a snapshot of a resource that
was matched cheaply, it reads it just before running the operation.

## Example

```python
--8<--
examples/04_fingerprints.py
--8<--
```

```text
--8<--
examples/output/04_fingerprints.txt
--8<--
```
