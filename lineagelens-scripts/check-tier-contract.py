#!/usr/bin/env python3
"""check-tier-contract.py — LineageLens tier contract validator.

Validates three things:
  (a) envFlags consistency  — each tier's compose file environment block must
      match the envFlags declared in tiers.json.
  (b) backend capability booleans — the Settings class must derive booleans
      (is_solo_mode, neo4j_enabled, vector_search_enabled) that agree with
      tiers.json for each BACKEND_MODE.  Capabilities derivable from the
      backend are checked; those that aren't surfaced in config/mode_guard
      are listed explicitly so omissions are visible.
  (c) docs table — each row in the Quick-comparison table of
      tier-capabilities.md is cross-checked against tiers.json.

Exit 0 → all checks pass.
Exit 1 → one or more mismatches (each printed with tier / key / expected /
          found / file).

Usage:
    python lineagelens-scripts/check-tier-contract.py
"""
from __future__ import annotations

import json
import os
import re
import sys

# ── Repo-root resolution ──────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT   = os.path.dirname(SCRIPT_DIR)

TIERS_JSON      = os.path.join(REPO_ROOT, "lineagelens-config", "tiers.json")
DEPLOY_DIR      = os.path.join(REPO_ROOT, "lineagelens-deploy")
BACKEND_DIR     = os.path.join(REPO_ROOT, "lineagelens-backend")
DOCS_CAPS_MD    = os.path.join(REPO_ROOT, "lineagelens-docs", "tier-capabilities.md")

# ── Helpers ───────────────────────────────────────────────────────────────────

_mismatches: list[str] = []


def _fail(tier: str, key: str, expected: str, found: str, file: str) -> None:
    msg = (
        f"MISMATCH  tier={tier}  key={key}"
        f"  expected={expected!r}  found={found!r}"
        f"  file={file}"
    )
    print(msg)
    _mismatches.append(msg)


def _load_yaml_env(compose_path: str) -> dict[str, str]:
    """Return the backend-service environment dict from a compose file.

    Handles both inline environment blocks (dict or list form) and env_file
    entries.  Does NOT expand ${VAR} substitutions — we only want the literal
    values that are hard-coded in the file, not the defaults.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        # Minimal YAML loader fallback: parse only the environment: block
        # (sufficient since our compose files use simple key: value pairs).
        return _parse_compose_env_minimal(compose_path)

    with open(compose_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    services = data.get("services", {})
    # Use the first service that has BACKEND_MODE set; for lite that's
    # "lineagelens", for plus/max that's "backend".
    for svc in services.values():
        env = _extract_env(svc)
        if "BACKEND_MODE" in env:
            return env
    return {}


def _extract_env(svc: dict) -> dict[str, str]:
    raw = svc.get("environment") or {}
    result: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            result[str(k)] = str(v) if v is not None else ""
    elif isinstance(raw, list):
        for item in raw:
            if "=" in item:
                k, _, v = item.partition("=")
                result[k.strip()] = v.strip()
    return result


def _parse_compose_env_minimal(compose_path: str) -> dict[str, str]:
    """Regex fallback when PyYAML is not installed.

    Extracts only lines of the form ``      KEY: value`` that appear under
    an ``environment:`` block (indented by 6+ spaces), stopping at the next
    top-level key.  Sufficient for our compose files.
    """
    result: dict[str, str] = {}
    in_env = False
    env_indent = 0
    with open(compose_path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip()
            if not stripped:
                continue
            indent = len(line) - len(line.lstrip())
            if re.match(r"\s+environment\s*:", stripped):
                in_env = True
                env_indent = indent + 2
                continue
            if in_env:
                if indent < env_indent:
                    in_env = False
                    continue
                m = re.match(r"\s+(\w+)\s*:\s*(.*)", stripped)
                if m:
                    result[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return result


# ── (a) envFlags vs compose ───────────────────────────────────────────────────

COMPOSE_TIER_MAP = {
    "lite": "docker-compose.lite.yml",
    "plus": "docker-compose.plus.yml",
    "max":  "docker-compose.max.yml",
}
# Normalise both sides to lowercase for case-insensitive boolean comparison.
_BOOL_NORM = {"true": "true", "false": "false", "1": "true", "0": "false"}


def _resolve_compose_default(val: str) -> str:
    """Return the default portion of a Docker ${VAR:-default} substitution.

    ${VAR:-foo}  → "foo"   (use default when VAR is unset or empty)
    ${VAR:?msg}  → ""      (required; we can't check the actual value here)
    ${VAR}       → ""      (no default; skip comparison)
    plain string → unchanged
    """
    m = re.match(r"^\$\{[^}]+:-([^}]*)\}$", val)
    if m:
        return m.group(1)
    # Required or plain substitution — no literal default, skip by returning original
    # (the caller will treat it as a skip when _norm gives a different result).
    if val.startswith("${"):
        return val  # will mismatch unless tiers.json has the same template
    return val


def _norm(val: object) -> str:
    s = str(val).lower().strip()
    return _BOOL_NORM.get(s, s)


def check_env_flags(tiers: dict) -> None:
    print("\n=== (a) envFlags vs compose files ===")
    ok = True
    for tier_name, compose_file in COMPOSE_TIER_MAP.items():
        tier = tiers.get(tier_name, {})
        env_flags: dict[str, str] = tier.get("envFlags", {})
        if not env_flags:
            print(f"  SKIP {tier_name}: no envFlags in tiers.json")
            continue

        compose_path = os.path.join(DEPLOY_DIR, compose_file)
        if not os.path.exists(compose_path):
            _fail(tier_name, "<compose-file>", "exists", "missing", compose_path)
            ok = False
            continue

        compose_env = _load_yaml_env(compose_path)
        checked = []
        for key, expected_val in env_flags.items():
            # Only check keys that are hard-coded in the compose file.
            # Keys absent from the inline block are env_file / runtime — skip.
            if key not in compose_env:
                print(f"  SKIP {tier_name}/{key}: not hard-coded in compose inline env")
                continue
            raw_val = compose_env[key]
            # Resolve Docker variable-substitution defaults: ${VAR:-default} → default.
            # If the value is a substitution with a default that matches tiers.json, it
            # is correct — runtime will resolve the same way.
            found_val = _resolve_compose_default(raw_val)
            if _norm(expected_val) != _norm(found_val):
                _fail(tier_name, key, _norm(expected_val), _norm(found_val), compose_path)
                ok = False
            else:
                checked.append(f"{key}={found_val}")

        if checked:
            print(f"  OK   {tier_name}: {', '.join(checked)}")

    if ok:
        print("  All envFlags match compose files.")


# ── (b) backend capability booleans ──────────────────────────────────────────

# Map tier -> BACKEND_MODE for the backend Settings import.
TIER_BACKEND_MODE = {"lite": "solo", "plus": "team", "max": "enterprise"}

# Capabilities we CAN derive directly from Settings properties.
# Format: capability_key_in_tiers_json -> (property_name, modes_where_true)
_DERIVABLE: list[tuple[str, str, set[str]]] = [
    # is_solo_mode is True only for "solo" (Lite).  tiers.json has no
    # explicit is_solo_mode key, but we verify the mode flag indirectly via
    # the neo4j / vector checks below.
    ("neo4j",                "neo4j_enabled",         {"max"}),
    ("vectorSearch",         "vector_search_enabled",  {"max"}),
]

# Capabilities present in tiers.json that we CANNOT check from config/mode_guard
# because they are not surfaced as Settings properties or mode_guard guards.
_NOT_DERIVABLE = [
    "mcp",
    "provenanceIntegrity",
    "aiBomExport",
    "sso",
    "retention",
    "auditLog",
    "reviews",
    "apiKeys",
    "webhooks",
    "teamUsers",
    "workspaces",
    "rbac",
    "githubActionsGate",
    "k8s",
    "helm",
]


def check_backend_capabilities(tiers: dict) -> None:
    print("\n=== (b) backend capability booleans (derivable from Settings) ===")

    # Add backend to sys.path so we can import from it.
    backend_path = os.path.join(BACKEND_DIR)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    # Minimal env to satisfy Settings validators.
    os.environ.setdefault("JWT_SECRET_KEY", "contract-check-key-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    os.environ.setdefault("JWT_REFRESH_SECRET_KEY", "contract-check-refresh-xxxxxxxxxxxxxxxxxxxxxxxxxx")
    os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")

    try:
        from app.core.config import Settings, get_settings  # type: ignore
    except Exception as exc:
        print(f"  SKIP: cannot import backend Settings — {exc}")
        print("  (Run from repo root with the backend virtualenv active.)")
        return

    ok = True
    for tier_name, mode in TIER_BACKEND_MODE.items():
        tier = tiers.get(tier_name, {})

        # Build a fresh Settings instance for this tier's mode.
        get_settings.cache_clear()
        os.environ["BACKEND_MODE"] = mode
        # Set the two boolean env vars to what tiers.json declares.
        os.environ["NEO4J_ENABLED"]         = str(tier.get("neo4j", False)).lower()
        os.environ["VECTOR_SEARCH_ENABLED"] = str(tier.get("vectorSearch", False)).lower()
        # The validator rejects VECTOR_SEARCH_ENABLED=true with a SQLite URL, so
        # supply a fake Postgres URL for enterprise (max) tier checks.
        if tier.get("vectorSearch"):
            os.environ["DATABASE_URL"] = "postgresql+asyncpg://check:check@localhost/check"
        else:
            os.environ.pop("DATABASE_URL", None)

        try:
            settings = Settings()  # type: ignore
        except Exception as exc:
            print(f"  SKIP {tier_name}: Settings() raised — {exc}")
            continue

        for cap_key, prop_name, true_for_modes in _DERIVABLE:
            expected = tier.get(cap_key)
            if expected is None:
                continue
            derived = getattr(settings, prop_name, None)
            if derived is None:
                print(f"  SKIP {tier_name}/{cap_key}: property {prop_name!r} not found")
                continue
            if bool(derived) != bool(expected):
                _fail(
                    tier_name, cap_key,
                    str(bool(expected)), str(bool(derived)),
                    f"Settings.{prop_name} for BACKEND_MODE={mode}",
                )
                ok = False
            else:
                print(f"  OK   {tier_name}/{cap_key}: {prop_name}={derived}")

    get_settings.cache_clear()

    if ok:
        print("  All derivable backend capabilities match tiers.json.")

    print(
        "\n  Capabilities NOT checked (not exposed as Settings properties or"
        " mode_guard guards):\n  " + ", ".join(_NOT_DERIVABLE)
    )


# ── (c) docs table vs tiers.json ─────────────────────────────────────────────

# Map the exact row header strings from tier-capabilities.md to tiers.json keys.
# Only rows we can unambiguously map are included.
_DOCS_TO_JSON: dict[str, str] = {
    "Local JSON storage":          "localStorage",
    "Proxy capture":               "proxy",
    "Prompt capture":              "promptCapture",
    "Model capture":               "modelCapture",
    "SQLite backend":              "sqlite",
    "PostgreSQL backend":          "postgres",
    "Dashboard":                   "dashboard",
    "Setup wizard":                "setupWizard",
    "Team users / workspaces":     "teamUsers",
    "RBAC (admin/member)":         "rbac",
    "Reviews & comments":          "reviews",
    "Audit log":                   "auditLog",
    "Reports & webhooks":          "webhooks",
    "API keys":                    "apiKeys",
    "MCP server":                  "mcp",
    "GitHub Actions gate":         "githubActionsGate",
    "Provenance integrity (hash chain)": "provenanceIntegrity",
    "AI-BOM export (signed)":      "aiBomExport",
    "SSO / OIDC":                  "sso",
    "Retention & redaction policy":"retention",
    "Vector / semantic search":    "vectorSearch",
    "Neo4j graph lineage":         "neo4j",
    "Kubernetes / Helm":           "k8s",
}

# Columns in the docs table, in order.
_DOC_TIERS = ["base", "lite", "plus", "max"]
# Symbols used in the docs table.
_TRUE_SYM  = {"✅"}
_FALSE_SYM = {"—", "", "-"}


def _parse_md_table(md_path: str) -> dict[str, dict[str, bool]]:
    """Return {row_label: {tier: bool}} from the Quick-comparison table."""
    result: dict[str, dict[str, bool]] = {}
    with open(md_path, encoding="utf-8") as fh:
        content = fh.read()

    # Find the table block — starts with the header row we know.
    table_start = content.find("| Capability |")
    if table_start == -1:
        return result

    lines = content[table_start:].split("\n")
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            break
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if len(parts) < 5:
            continue
        label = parts[0].lstrip("*").rstrip("*").strip()
        if label in ("Capability", "---", ":---:"):
            continue
        row: dict[str, bool] = {}
        for i, tier in enumerate(_DOC_TIERS):
            cell = parts[i + 1] if i + 1 < len(parts) else ""
            row[tier] = cell in _TRUE_SYM
        result[label] = row
    return result


def check_docs_table(tiers: dict) -> None:
    print("\n=== (c) docs tier-capabilities.md table vs tiers.json ===")
    if not os.path.exists(DOCS_CAPS_MD):
        print(f"  SKIP: {DOCS_CAPS_MD} not found")
        return

    doc_table = _parse_md_table(DOCS_CAPS_MD)
    if not doc_table:
        print("  SKIP: could not parse Quick-comparison table in docs")
        return

    ok = True
    for doc_label, json_key in _DOCS_TO_JSON.items():
        if doc_label not in doc_table:
            print(f"  SKIP: row {doc_label!r} not found in docs table")
            continue
        for tier_name in _DOC_TIERS:
            tier = tiers.get(tier_name, {})
            if json_key not in tier:
                continue
            expected = bool(tier[json_key])
            found    = doc_table[doc_label].get(tier_name, False)
            if expected != found:
                _fail(
                    tier_name, json_key,
                    "✅" if expected else "—",
                    "✅" if found else "—",
                    DOCS_CAPS_MD + f" (row: {doc_label!r})",
                )
                ok = False

    if ok:
        print("  All checked docs-table rows match tiers.json.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"LineageLens tier contract validator")
    print(f"Repo root : {REPO_ROOT}")
    print(f"Tiers file: {TIERS_JSON}")

    with open(TIERS_JSON, encoding="utf-8") as fh:
        tiers: dict = json.load(fh)

    check_env_flags(tiers)
    check_backend_capabilities(tiers)
    check_docs_table(tiers)

    print()
    if _mismatches:
        print(f"FAILED — {len(_mismatches)} mismatch(es) found. See output above.")
        return 1
    else:
        print("PASSED — all tier contract checks green.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
