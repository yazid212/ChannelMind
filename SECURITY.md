# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities through a private GitHub security
advisory when this repository is published. Do not include credentials,
personal data, or exploit details in a public issue.

Include the affected version or commit, impact, reproduction conditions, and
the smallest safe proof needed to validate the report. Maintainers should
acknowledge reports within seven days and coordinate disclosure after a fix is
available.

## Deployment assumptions

This project uses expiring, signed anonymous workspaces. Before exposing it to
the internet, configure the production Redis-backed rate limits and verify
client IP handling behind the hosting proxy. Restrict provider keys by service
and quota where supported, set billing alerts, and keep Redis and Qdrant
backups. Never commit `.env` or cached transcript data.
