# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| Latest (main) | Yes |
| Older releases | Best-effort |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Email **karnatipraveen17@gmail.com** with:

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof of concept
- Which component is affected (extension, backend, proxy, MCP server)
- The tier(s) affected (Base / Lite / Plus / Max)

You'll receive an acknowledgment within 48 hours and a status update within 7 days.

## Scope

In scope:
- Authentication bypass in the FastAPI backend
- Prompt/credential leakage through the proxy
- Privilege escalation between team roles (Plus/Max)
- Injection vulnerabilities in search or ingest endpoints
- Token handling in the VS Code extension

Out of scope:
- Denial of service against a self-hosted deployment you don't own
- Issues in third-party dependencies that are already publicly known
- Social engineering

## Security architecture notes

- Passwords hashed with PBKDF2-SHA256 (390,000 iterations)
- JWT access tokens (15 min expiry) + refresh tokens (7 days)
- Rate limiting on all auth endpoints (in-memory or Redis-backed)
- Audit log captures every action with IP, user, and timestamp
- Proxy does not modify request bodies — it reads and forwards them
- All secrets are configurable via environment variables, never hardcoded
- TLS termination is the operator's responsibility (nginx/caddy recommended)

## Responsible disclosure

We follow a **90-day coordinated disclosure** policy. We will credit researchers who report valid vulnerabilities in the changelog and README unless they prefer to remain anonymous.
