# Concepts

This page introduces the vocabulary used throughout the documentation and the API.

## Resources and sources

A **resource** is anything your application keeps in sync: a document, a
configuration entry, a file, a plugin manifest, a database row. Each one is
identified by a stable, unique `resource_id`.

A **source** implements [`ResourceSource`][refresh_engine.ResourceSource]:

- `discover()` lists the current resources as [`Resource`][refresh_engine.Resource]
  values, which carry only cheap information: the ID, optional metadata, an optional
  version label, optional dependencies, and an optional cheap change indicator. It
  returns them in a [`DiscoveryResult`][refresh_engine.DiscoveryResult] together
  with a `complete` flag.
- `snapshot(resource)` reads one resource in full and returns a
  [`ResourceSnapshot`][refresh_engine.ResourceSnapshot] with its content and
  metadata.

!!! warning "Completeness protects against accidental deletion"
    Only a discovery that reports `complete=True` lets the engine conclude that a
    previously known resource was deleted. Report `complete=False` whenever the
    listing may be partial, for example after a pagination error. `complete` must be
    an actual `bool`; a value such as the string `"false"` raises a
    `ConfigurationError` instead of being treated as true.

## Operations

An **operation** is an asynchronous function or a subclass of
[`RefreshOperation`][refresh_engine.RefreshOperation]. The engine calls it once per
planned resource with a
[`PlanAction`][refresh_engine.PlanAction]:

- `REFRESH` passes the discovered resource and its current snapshot;
- `DELETE` passes a resource that carries only the ID of a resource that a complete
  discovery no longer reports, and no snapshot.

An operation reports failure by raising an exception. Because a retry, a timeout,
or a restart can run it again, an operation should be idempotent.

## Fingerprints and state

A **fingerprint** is a deterministic digest of a snapshot, produced by a
[`FingerprintStrategy`][refresh_engine.FingerprintStrategy]. The default
[`CompositeHash`][refresh_engine.CompositeHash] covers the content, metadata,
version, version token, and ETag of the snapshot.

After an operation succeeds, the engine stores a
[`ResourceState`][refresh_engine.ResourceState] for the resource: its fingerprint,
version, cheap indicator, snapshot metadata, dependencies, and the time of the
refresh. A [`StateStore`][refresh_engine.StateStore] keeps these states between
refreshes; the default [`InMemoryStateStore`][refresh_engine.InMemoryStateStore]
keeps them for the lifetime of the process.

## Immutable values

Models are frozen, and so is the metadata they carry. The metadata of resources,
snapshots, states, and refresh requests, and the attributes of events, are copied on
construction, so later changes to the caller's objects never leak into them:

- `dict` and mapping proxies become read-only dictionaries, `list` becomes a
  read-only list, `set` becomes `frozenset`, and tuples are rebuilt, at any depth;
  the copies still compare equal to the originals, serialize like them, and have
  the same fingerprints;
- mutating a copy raises `TypeError`;
- other objects, including subclasses of built-in containers, are kept by
  reference, because the engine cannot copy them safely; treat them as read-only;
- metadata that contains a reference cycle raises a `ConfigurationError`.

Snapshot `content` is never copied, so large payloads cost nothing extra. The engine
only reads it; treat it as read-only once returned from `snapshot()`.

## Change detection

Every refresh compares what it observes with the stored state:

![Change classification](assets/diagrams/change-detection-light.png#only-light)
![Change classification](assets/diagrams/change-detection-dark.png#only-dark)

| Change | Meaning |
| --- | --- |
| Added | The resource has no stored state. |
| Modified | Its fingerprint differs from the stored one. |
| Dependency changed | Its fingerprint is unchanged, but it depends on different resources. |
| Unchanged | Neither the fingerprint nor the dependencies changed. |
| Deleted | It has stored state, but a complete discovery no longer reports it. |

A **cheap indicator**, such as an ETag or a modification time, lets the engine skip
reading a snapshot: when a resource marks its indicator as reliable and the
indicator equals the stored one, the stored fingerprint is reused.

## Refresh modes

A [`RefreshMode`][refresh_engine.RefreshMode] decides which resources receive an
operation:

| Mode | Refreshes |
| --- | --- |
| `INCREMENTAL` | Added, modified, and dependency-changed resources, plus deletions. |
| `DEPENDENCY_AWARE` | The incremental set plus every resource that transitively depends on it. |
| `FULL` | Every discovered resource, plus deletions. |
| `TARGETED` | The resources chosen by IDs or a selector, regardless of change. |

See [Refresh modes and targeting](guides/refresh-modes.md).

## Results, statuses, and issues

Every refresh returns a [`RefreshResult`][refresh_engine.RefreshResult] with counts,
timings, per-operation outcomes, warnings, and issues. Failures are reported in the
result instead of being raised, each as a
[`RefreshIssue`][refresh_engine.RefreshIssue] that names the
[`RefreshPhase`][refresh_engine.RefreshPhase] where it happened.

| Status | When |
| --- | --- |
| `SUCCESS` | Nothing failed. |
| `PARTIAL` | Some resources failed, but at least one in-scope resource was handled. |
| `FAILED` | The refresh could not complete, the fail-fast policy stopped it, or every in-scope resource failed. |
| `CANCELLED` | The refresh was superseded by a newer request or cancelled by shutdown. |
| `SKIPPED` | The request was not run because another refresh was running. |

## Lifecycle events

The engine publishes a [`RefreshEvent`][refresh_engine.RefreshEvent] at every step
of a refresh. Subscribe handlers to an
[`AsyncEventPublisher`][refresh_engine.AsyncEventPublisher] to log, measure, or react to
refreshes. See [Events, logging, and metrics](guides/observability.md).
