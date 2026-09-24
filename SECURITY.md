# Security Policy

## Supported versions

The project is currently in alpha. Security fixes are targeted at the latest public release and the current `main` branch unless a maintainer explicitly states otherwise.

## Secrets and local data

Do not publish API keys, tokens, credentials, production databases, real runs, private assets, or backups. The project reserves `.local/` for private runtime data and Git ignores that directory by default.

If a credential reaches a commit, treat it as compromised even if the commit is later removed. Revoke or rotate it with the corresponding provider.

## Reporting a vulnerability

Do not publish exploitable details, credentials, or private data in a public issue. Use GitHub private vulnerability reporting when it is enabled. If no private reporting channel is available, open an issue without sensitive technical details and request a private contact method.

Include the affected release, component, minimum reproduction steps, and observed impact in the report. Avoid including real credentials or private customer data in reproduction material.
