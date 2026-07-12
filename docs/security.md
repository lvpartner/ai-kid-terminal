# Security and Threat Model

Protected assets are children's conversation metadata, device identity, admin control, provider credentials, and update files. Relevant attackers include an unauthenticated Internet client, a stolen device token, a malicious local app, and an operator mistake.

Controls include one-time expiring enrollment, HMAC-hashed revocable/rotatable device tokens, a separate admin key, constant-time comparisons, strict Pydantic input limits, WebSocket size/rate/idle limits, deterministic rollout authorization, safe basenames, published-only downloads, and pre-publication SHA-256 verification. Logs are JSON and redact authorization, tokens, API keys, passwords, content, and common child identifiers. Raw audio is not stored. The database and release volumes are not public.

Production rejects short or missing secrets. `.env`, tokens, databases, releases, private keys, and backups are ignored by Git. Compose runs the API as UID 10001 with a read-only filesystem, no Linux capabilities, and no-new-privileges. PostgreSQL uses a dedicated non-superuser application account and no exposed host port.

Residual risks: SHA-256 establishes file integrity, not publisher identity; Android must pin and validate the fixed release signing certificate. Basic regex redaction is not a complete DLP system. A compromised server or admin key remains high impact. Add key rotation, encrypted backups, network ingress controls, external monitoring, and a reviewed TLS proxy before production. Never disable the host firewall or alter SSH for this service.

