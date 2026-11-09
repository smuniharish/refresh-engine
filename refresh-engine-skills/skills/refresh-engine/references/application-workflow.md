# Application workflow

Use this route when connecting an application's source and derived data to
refresh-engine.

1. Identify which resources are represented in the derived data and which
   async operation applies each refresh result.
2. Implement `ResourceSource` and any other required extension objects by
   subclassing their ABCs. The operation itself may be a plain async function.
3. Use the installed package's signatures and documentation for construction
   and invocation; this skill intentionally does not freeze method signatures.
4. Keep application-specific source and destination behavior in the
   integration, not in the skill's sample.

For examples of project domains, see the package's public examples and
documentation. Verify any copied code against the installed package version.
