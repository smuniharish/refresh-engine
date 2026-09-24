# Security policy

## Supported versions

The latest released minor version receives security fixes. Pre-release and older
minor versions may require upgrading before a fix can be applied.

## Reporting a vulnerability

Do not open a public issue. Open the repository's **Security** tab, select
**Advisories**, and choose **Report a vulnerability** to create a private report.
Include affected versions, impact, reproduction steps, and any proposed mitigation.
Do not include production secrets or personal data. If private vulnerability
reporting is unavailable, ask the repository owner to enable it before sharing
sensitive details.

Maintainers will acknowledge a report as soon as practical, coordinate validation
and remediation privately, and disclose after a fix is available. Avoid public
disclosure until that coordination is complete.

## Operational scope

Applications control source access, operation side effects, persistence, logging, and
secrets. Treat resource content and metadata as untrusted. Use timeouts, bounded
concurrency, least-privilege credentials, a redacting event handler, and a durable
transactional store appropriate to the deployment.
