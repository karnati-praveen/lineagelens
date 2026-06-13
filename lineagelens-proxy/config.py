"""Environment-variable configuration for the LineageLens proxy."""
import os
import re
import sys as _sys
import urllib.parse

BACKEND_URL       = os.environ.get("BACKEND_URL", "http://backend:8787").rstrip("/")
INGEST_TOKEN      = os.environ.get("BACKEND_INGEST_TOKEN", "")
WORKSPACE_ID      = os.environ.get("PROXY_WORKSPACE_ID", "proxy-capture")
PROXY_PORT        = int(os.environ.get("PROXY_PORT", "8788"))
PROXY_HOST        = os.environ.get("PROXY_HOST", "127.0.0.1")
MAX_BODY_BYTES    = int(os.environ.get("PROXY_MAX_BODY_BYTES", "2000000"))

# Built-in patterns for common secret shapes.
_DEFAULT_REDACT_PATTERN_STRINGS = [
    r"sk-[A-Za-z0-9_-]{16,}",                       # OpenAI / generic sk- keys
    r"sk-ant-[A-Za-z0-9_-]{16,}",                   # Anthropic keys
    r"AIza[0-9A-Za-z_-]{20,}",                       # Google API keys
    r"ya29\.[0-9A-Za-z_-]+",                         # Google OAuth access tokens
    r"gh[pousr]_[A-Za-z0-9]{20,}",                   # GitHub tokens (ghp_, gho_, etc.)
    r"github_pat_[A-Za-z0-9_]{20,}",                 # GitHub fine-grained PATs
    r"xox[baprs]-[A-Za-z0-9-]{10,}",                 # Slack tokens
    r"AKIA[0-9A-Z]{16}",                             # AWS access key IDs
    r"(?i)bearer\s+[A-Za-z0-9._~+/-]{20,}=*",        # Bearer tokens
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # JWTs
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",     # private keys
]

_REDACT_PATTERNS_RAW = [p.strip() for p in os.environ.get("PROXY_REDACT_PATTERNS", "").split(",") if p.strip()]
if os.environ.get("PROXY_DISABLE_DEFAULT_REDACTION", "").strip().lower() not in {"1", "true", "yes"}:
    _REDACT_PATTERNS_RAW = _DEFAULT_REDACT_PATTERN_STRINGS + _REDACT_PATTERNS_RAW
REDACT_PATTERNS: list[re.Pattern] = []
for _rp in _REDACT_PATTERNS_RAW:
    try:
        REDACT_PATTERNS.append(re.compile(_rp))
    except re.error as _rp_err:
        print(f"[lineagelens-proxy] WARNING: ignoring invalid PROXY_REDACT_PATTERNS entry {_rp!r}: {_rp_err}", file=_sys.stderr)

# CONNECT tunnel server (for tools that use HTTPS_PROXY / HTTP_PROXY)
PROXY_CONNECT_PORT  = int(os.environ.get("PROXY_CONNECT_PORT", "8789"))
PROXY_CA_CERT_PATH  = os.environ.get("PROXY_CA_CERT_PATH", "")
PROXY_CA_KEY_PATH   = os.environ.get("PROXY_CA_KEY_PATH", "")
PROXY_CONNECT_TOKEN = os.environ.get("PROXY_CONNECT_TOKEN", "").strip()

# Hostnames always blocked as CONNECT targets.
_BLOCKED_CONNECT_HOSTS = frozenset({
    "localhost", "ip6-localhost", "ip6-loopback", "broadcasthost", "0.0.0.0",
})

# Well-known LLM API domains.
_KNOWN_LLM_DOMAINS = frozenset({
    "api.anthropic.com",
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "api.together.xyz",
    "api.groq.com",
    "api.fireworks.ai",
    "api.mistral.ai",
    "openai.azure.com",
})

MAX_RESPONSE_BODY_BYTES = MAX_BODY_BYTES


def _backend_url_points_to_proxy() -> bool:
    """Return True if BACKEND_URL resolves to the proxy itself (loop guard)."""
    try:
        parsed = urllib.parse.urlparse(BACKEND_URL)
    except Exception:
        return False

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False

    try:
        port = parsed.port
    except ValueError:
        return False

    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    proxy_hosts = {PROXY_HOST.strip().lower(), "localhost", "127.0.0.1", "0.0.0.0", "::1"}
    return port == PROXY_PORT and host in proxy_hosts
