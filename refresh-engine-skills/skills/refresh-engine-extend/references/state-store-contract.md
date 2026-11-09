# State-store contract

Implement a durable state backend by subclassing `StateStore` and using the
`StateTransaction` ABC as required by the installed public API.

- `StateStore.transaction()` is awaited.
- The reusable store contract is `refresh_engine.testing.StateStoreContract`.
- Use the contract when checking a custom store; consult the installed
  `StateStoreContract` signatures for its test integration details.

Avoid inferring a database schema, transaction method list, serialization
format, or concurrency guarantees from the ABC names alone. Keep backend
choices and guarantees explicit in the integration's own documentation.
