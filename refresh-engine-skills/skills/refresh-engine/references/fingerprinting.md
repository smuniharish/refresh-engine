# Fingerprinting

Source: `src/refresh_engine/detection/fingerprints.py`,
`docs/guides/fingerprints-and-dependencies.md`.

## Strategies (`FingerprintStrategy` / `FingerprintStrategyABC`)

| Strategy | Hashes | Raises when |
| --- | --- | --- |
| `CompositeHash(include_dependencies=True)` (default) | content, metadata, version, version_token, etag, and (if enabled) sorted dependency IDs | never (all fields optional) |
| `ContentHash` | `snapshot.content` only | never |
| `MetadataHash` | `snapshot.metadata` only | never |
| `VersionTokenFingerprint` | `snapshot.version_token` | `FingerprintError` if `version_token is None` |
| `ETagFingerprint` | `snapshot.etag` | `FingerprintError` if `etag is None` |

All produce a `ResourceFingerprint(algorithm=f"sha256:{namespace}", value=<hex digest>)`
over a **canonical JSON encoding** (`refresh_engine.detection.fingerprints.canonical_bytes`,
internal helper) that tags every value with its Python type so that, e.g.,
`1` (int) and `1.0` (float) and `"1"` (str) never collide.

## Supported fingerprintable types

`None`, `bool`, `int`, `float` (must be finite — `inf`/`nan` raise
`FingerprintError`), `str`, `bytes`, `datetime`/`date`, `pathlib.PurePath`,
`Enum`, dataclass instances (recursively, via `dataclasses.asdict`),
`Mapping` (keys+values canonicalized and sorted), `Set` (canonicalized and
sorted), and `Sequence` (order-preserving). Any other type raises
`FingerprintError` — do not fingerprint open file handles, sockets, thread
locks, or arbitrary custom objects without first converting them into one of
these supported shapes.

**Never include:** wall-clock timestamps, random values, or anything whose
value changes without the underlying resource actually changing — doing so
defeats change detection by making every refresh look `MODIFIED`.

## Choosing a strategy

- Use the default `CompositeHash` unless you have a specific reason not to.
  It also automatically picks up dependency changes into
  `ChangeType.DEPENDENCY_CHANGED` when content/metadata/version/etag are all
  unchanged but the dependency set changed.
- Use `ContentHash` when metadata is high-churn but functionally irrelevant to
  refresh behavior (e.g. request timing metadata you don't want to trigger a
  refresh).
- Use `VersionTokenFingerprint` or `ETagFingerprint` when the source system
  already guarantees a monotonically-changing, collision-free token/etag —
  this avoids hashing large content bodies at all, at the cost of trusting the
  source's token semantics.
- Write a custom `FingerprintStrategy`/`FingerprintStrategyABC` when your
  content type needs bespoke normalization before hashing (e.g. sorting an
  otherwise-unordered list field that has no semantic order).

```python
from refresh_engine import ContentHash, RefreshEngine, RefreshMode

engine = RefreshEngine(source, operation, fingerprint=ContentHash())
result = await engine.refresh(mode=RefreshMode.DEPENDENCY_AWARE)
print(result.modified_count, result.impacted_count)
```

## Cheap indicators vs strong fingerprints — do not confuse these

`Resource.cheap_indicator` / `cheap_indicator_reliable` are a **pre-check**,
evaluated during discovery, before any snapshot or fingerprint work happens.
They are not a `FingerprintStrategy` and are not pluggable the same way.

- `cheap_indicator: str | None` — a lightweight signal from discovery (e.g. a
  provider's list-API `etag`/`mtime`/`version` field that is cheaper to obtain
  than a full read).
- `cheap_indicator_reliable: bool` — set `True` **only** when equality of this
  value with the previously stored one *proves* the resource cannot have
  changed. If the discovery API can return a stale or non-monotonic value, do
  not mark it reliable.

When `cheap_indicator_reliable=True` and the current value equals the stored
`ResourceState.cheap_indicator`, the engine skips `snapshot()` and
`fingerprint()` for that resource entirely and carries forward the previous
`ResourceState.fingerprint` unchanged. This is the single biggest lever for
avoiding expensive reads at scale — cheaper than even a fast local hash,
because it avoids the read itself.

**Architecture decision (verified in `docs/architecture/decisions.md`):**
"Incorrectly declaring an indicator reliable can hide changes and is therefore
a source contract violation." Treat `cheap_indicator_reliable=True` as a
correctness contract, not a performance knob to flip optimistically.

## Custom fingerprint extension

```python
from refresh_engine import FingerprintStrategyABC, ResourceFingerprint, ResourceSnapshot
import hashlib

class SortedListHash(FingerprintStrategyABC):
    async def fingerprint(self, snapshot: ResourceSnapshot) -> ResourceFingerprint:
        items = sorted(snapshot.content.get("tags", []))
        digest = hashlib.sha256(",".join(items).encode()).hexdigest()
        return ResourceFingerprint("sha256:sorted-tags", digest)
```

Any object satisfying the `FingerprintStrategy` protocol (an `async def
fingerprint(self, snapshot) -> ResourceFingerprint` method) also works without
inheriting the ABC.
