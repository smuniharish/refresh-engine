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

Fingerprints from different strategies never match, so switching strategies makes
the next refresh report every resource as modified once.

## Canonical hashing

The built-in hash strategies use [`canonical_digest`][refresh_engine.canonical_digest],
which encodes a value into a canonical, type-tagged, length-prefixed byte string and
returns its SHA-256 digest. Two values have the same digest exactly when they have
the same canonical form, which is the same kind of value with the same contents.

Some different Python values intentionally share a canonical form:

- mapping insertion order and set iteration order do not matter;
- every mapping encodes alike (`dict`, `MappingProxyType`, and other `Mapping`
  types), and so does every set and every sequence, so `list` and `tuple` are
  interchangeable;
- `bytes`, `bytearray`, and `memoryview` are interchangeable;
- subclasses of `str`, `int`, `float`, `bytes`, dates, times, `Decimal`, and `UUID`
  encode as their base type;
- pure paths encode as their POSIX form, so `PureWindowsPath("a\\b")` and
  `PurePosixPath("a/b")` match.

Everything else is told apart, even values that compare equal:

- `1`, `1.0`, `True`, `Decimal(1)`, and `"1"` all differ, because the kind of value
  is part of the encoding;
- `0.0` and `-0.0` differ, and floats are encoded as exact IEEE 754 doubles;
- decimals encode as written, so `Decimal("1.0")` and `Decimal("1.00")` differ;
- datetimes encode in ISO 8601 with their offset, so a naive and an aware datetime
  differ, and so do two aware datetimes for the same instant in different time
  zones; normalize to UTC first if only the instant matters;
- enumerations and dataclasses carry their qualified type name, so two enumerations
  with equal values differ, and renaming or moving such a type changes its digest;
- integers of any size are supported.

Supported values are `None`, `bool`, `int`, finite `float`, `str`, binary types,
finite `Decimal`, `UUID`, dates, times, `timedelta`, pure paths, enumerations,
dataclass instances, and mappings, sets, and sequences of supported values. Any
other type, NaN, infinities, self-referencing structures, and values nested too
deeply for the interpreter's recursion limit raise
[`FingerprintError`][refresh_engine.FingerprintError], which fails only the affected
resource.

### Digest stability

Fingerprints are persisted in the state store, so the encoding is part of the
package's compatibility contract. A digest depends only on the canonical form above:
it is the same in every process, on every platform, and on every supported Python
version, regardless of hash randomization. The encoding is never changed within a
strategy's algorithm name. A future encoding would use a new algorithm name, such as
`sha256:composite-v2`; because fingerprints from different algorithms never match,
the first refresh after such a change would report every resource as modified once,
exactly as when you switch strategies.

### Writing a strategy

Implement [`FingerprintStrategy`][refresh_engine.FingerprintStrategy], or subclass
[`FingerprintStrategy`][refresh_engine.FingerprintStrategy], and hash exactly
the values that matter:

```python
from refresh_engine import (
    FingerprintStrategy,
    ResourceFingerprint,
    ResourceSnapshot,
    canonical_digest,
)


class PublishedFields(FingerprintStrategy):
    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        article = snapshot.content
        published = {"title": article["title"], "body": article["body"]}
        return ResourceFingerprint("sha256:published", canonical_digest(published))
```

Equal snapshots must always produce equal fingerprints. Never include clocks,
random values, or object identities.

`fingerprint()` is a coroutine, but the built-in strategies hash on the event loop.
For very large snapshots, hash in a thread with
`await asyncio.to_thread(canonical_digest, value)` so other work keeps running.

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
    changes, and whenever dependencies that only the snapshot declares change. An
    indicator that can stay the same across a change hides that change until the
    indicator moves.

`cheap_indicator_reliable` must be an actual `bool`; any other value, including the
string `"false"`, raises a `ConfigurationError`, so a misconfigured source can never
skip snapshots by accident.

A cheap match also skips the snapshot's dependencies. The resource keeps its stored
dependencies unless discovery declares new ones, so declare dependencies in discovery
whenever the source knows them without reading the resource. Otherwise, make sure the
indicator changes when they do.

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
