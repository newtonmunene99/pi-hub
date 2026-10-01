# Security policy

## Supported versions

Security fixes go into the latest release. Please update before reporting.

## Reporting a vulnerability

**Please don't open a public issue for security problems.**

Report privately through GitHub: **Security → Report a vulnerability** on this repository ([private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)). Include:

- what an attacker can do, and what access they need (same LAN, settings password, etc.);
- steps to reproduce, ideally against `PIHUB_DEMO=1`;
- the version or commit you tested.

You should get a reply within a week. Once a fix is released you'll be credited in the advisory unless you'd rather not be.

## Threat model

pi-hub is designed for a trusted home network:

- The dashboard is readable by anyone who can reach it. Expose it to the internet only behind a reverse proxy with authentication.
- API keys are stored server-side only and are never returned by the API. Changing a service's address drops its stored keys so they can't be redirected.
- The settings page can be protected with `PIHUB_PASSWORD` or disabled with `PIHUB_READONLY=1`.

Reports that break these guarantees — leaking a stored secret, writing settings without the password, reading files outside the UI folder, script injection into the dashboard — are especially welcome.
