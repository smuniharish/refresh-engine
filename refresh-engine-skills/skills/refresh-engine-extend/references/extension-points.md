# Extension points

The planned public extension objects are ABCs:

- `ResourceSource`
- `RefreshOperation`
- `FingerprintStrategy`
- `StateStore`
- `StateTransaction`
- `ResourceSelector`
- `MetricsSink`
- `EventHandler`
- `EventPublisher`

Use the installed package to inspect exact abstract methods and signatures;
this guide intentionally avoids freezing them while the API may still be
settling. All extension objects subclass their corresponding ABC, except that
an operation may instead be a plain async function and an event handler may
instead be a plain async function.

`AsyncEventPublisher` is the queued concrete publisher. This list describes
the public type names, not undocumented constructor arguments or lifecycle
behavior.
