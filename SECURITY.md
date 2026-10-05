# Security policy

## Supported versions

Security fixes are released for the latest minor version.

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| 0.1.x | No; upgrade to 0.2 |

## Reporting a vulnerability

Do not report vulnerabilities in public issues. Use
[private vulnerability reporting](https://github.com/smuniharish/refresh-engine/security/advisories/new)
instead, and include:

- the affected versions;
- the impact, and how an attacker could exploit it;
- steps or code to reproduce it;
- a proposed fix or mitigation, if you have one.

Do not include production secrets or personal data. The maintainer acknowledges
reports as soon as possible, coordinates the fix privately, and publishes an
advisory when a fixed release is available. Please keep the report confidential
until then.

## Securing an integration

refresh-engine runs code that the application provides: the source, the operation,
the state store, and event handlers. The application is responsible for the
credentials they use and the systems they change. In production:

- treat resource content and metadata as untrusted input;
- bound every external call with timeouts, and limit concurrency to what the
  systems involved can sustain;
- use credentials with the least privilege the source, operation, and state store
  need;
- keep secrets out of request metadata and events, and log through a redacting
  handler such as `StructlogEventHandler` with the default redactor;
- use a durable, transactional state store.
