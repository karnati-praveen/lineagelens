#!/usr/bin/env python3
"""LineageLens MCP Server

Exposes AI provenance tools to any MCP-capable client (Claude Code, Cursor, Continue, etc.).
Connects to a running LineageLens backend and surfaces search, record lookup,
governance insights, and plain-English explanations as MCP tools.

Environment variables:
    LINEAGELENS_BACKEND_URL   Backend base URL (auto-discovered if not set)
    LINEAGELENS_ACCESS_TOKEN  Pre-obtained JWT access token  -- OR --
    LINEAGELENS_USERNAME      LineageLens account username
    LINEAGELENS_PASSWORD      LineageLens account password
"""
import json
import logging
import os
import urllib.parse
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

_BACKEND_URL_ENV: str = os.environ.get("LINEAGELENS_BACKEND_URL", "").strip().rstrip("/")
_STATIC_TOKEN: str = os.environ.get("LINEAGELENS_ACCESS_TOKEN", "").strip()
_USERNAME: str = os.environ.get("LINEAGELENS_USERNAME", "").strip()
_PASSWORD: str = os.environ.get("LINEAGELENS_PASSWORD", "").strip()

SEARCH_ENDPOINT = "/search"

_DEFAULT_BACKEND_CANDIDATES = [
    "http://localhost:8787",
    "http://127.0.0.1:8787",
]

# Resolved at startup; falls back to first candidate if discovery fails
BACKEND_URL: str = _BACKEND_URL_ENV or _DEFAULT_BACKEND_CANDIDATES[0]

mcp = FastMCP(
    "lineagelens-mcp",
    instructions=(
        "LineageLens MCP Server - AI code provenance query interface.\n\n"
        "Token lifecycle: Tokens are cached in memory and refreshed automatically on 401. "
        "If authentication fails persistently, restart the MCP server process — it will re-login.\n\n"
        "Setup: Set LINEAGELENS_USERNAME, LINEAGELENS_PASSWORD, LINEAGELENS_BACKEND_URL before starting. "
        "Remote backend: If LINEAGELENS_BACKEND_URL is not set, the server will auto-discover a local backend."
    ),
)

_cached_token: str | None = None
_backend_discovered: bool = False


async def discover_backend_url(client: httpx.AsyncClient) -> str | None:
    """Try common local endpoints and return the first healthy one."""
    for url in _DEFAULT_BACKEND_CANDIDATES:
        try:
            resp = await client.get(f"{url}/health", timeout=2.0)
            if resp.status_code == 200:
                logger.info("MCP: Auto-discovered backend at %s", url)
                return url
        except Exception:
            continue
    return None


async def _ensure_backend_url(client: httpx.AsyncClient) -> None:
    """Set BACKEND_URL via auto-discovery if it was not provided via env."""
    global BACKEND_URL, _backend_discovered
    if _BACKEND_URL_ENV or _backend_discovered:
        return
    discovered = await discover_backend_url(client)
    if discovered:
        BACKEND_URL = discovered
    _backend_discovered = True


async def _login(client: httpx.AsyncClient) -> str:
    if not _USERNAME or not _PASSWORD:
        raise RuntimeError(
            "Authentication required. Set LINEAGELENS_ACCESS_TOKEN or "
            "LINEAGELENS_USERNAME + LINEAGELENS_PASSWORD environment variables."
        )
    resp = await client.post(
        f"{BACKEND_URL}/auth/login",
        json={"username": _USERNAME, "password": _PASSWORD},
    )
    resp.raise_for_status()
    return resp.json()["accessToken"]


async def _get_valid_token(client: httpx.AsyncClient) -> str | None:
    global _cached_token
    if _STATIC_TOKEN:
        return _STATIC_TOKEN
    if _cached_token:
        return _cached_token
    try:
        token = await _login(client)
        _cached_token = token
        return token
    except Exception as exc:
        logger.error("MCP: Failed to authenticate with backend: %s", exc)
        return None


# Keep backward-compatible alias used by check_file_risk and other internal callers
async def _get_token() -> str:
    async with httpx.AsyncClient(timeout=12.0) as client:
        await _ensure_backend_url(client)
        token = await _get_valid_token(client)
    if token is None:
        raise RuntimeError(
            "Cannot authenticate with LineageLens backend. "
            "Check LINEAGELENS_USERNAME/PASSWORD and that the backend is running."
        )
    return token


_AUTH_ERROR_MSG = (
    "Authentication failed. To fix:\n"
    "1. Ensure LINEAGELENS_USERNAME and LINEAGELENS_PASSWORD are set\n"
    "2. Check the backend is running: curl http://localhost:8787/health\n"
    "3. Verify the user exists: lineagelens status"
)

_BACKEND_UNREACHABLE_MSG = (
    "Error: LineageLens backend is unreachable at {url}. "
    "Start it with  lineagelens start  and verify it is healthy at {url}/health."
)

_NEO4J_UNAVAILABLE_MSG = (
    "Lineage graph is disabled on this instance. Enable Neo4j to use this feature.\n"
    "See: lineagelens start --mode max"
)


def _handle_status_error(status_code: int, detail: str, url: str) -> str | None:
    """Return a user-friendly message for known HTTP error codes, or None to raise."""
    if status_code in (401, 403):
        return _AUTH_ERROR_MSG
    if status_code == 503 and "neo4j" in detail.lower():
        return _NEO4J_UNAVAILABLE_MSG
    return None


async def _req(method: str, path: str, **kwargs: Any) -> Any:
    global _cached_token
    full_url = f"{BACKEND_URL}{path}"

    async with httpx.AsyncClient(timeout=20.0) as client:
        await _ensure_backend_url(client)

        token = await _get_valid_token(client)
        if token is None:
            return _AUTH_ERROR_MSG  # return as string so tool can surface it

        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}

        try:
            resp = await client.request(method, full_url, headers=headers, **kwargs)
        except httpx.ConnectError:
            raise RuntimeError(_BACKEND_UNREACHABLE_MSG.format(url=BACKEND_URL))
        except httpx.TimeoutException:
            raise RuntimeError(
                f"Error: Request to LineageLens backend timed out ({full_url}). "
                "The backend may be overloaded or unresponsive."
            )

        if resp.status_code == 401 and not _STATIC_TOKEN:
            # Token expired — clear cache and retry login once
            _cached_token = None
            try:
                token = await _login(client)
                _cached_token = token
            except Exception as exc:
                logger.error("MCP: Token refresh failed: %s", exc)
                raise RuntimeError(_AUTH_ERROR_MSG) from exc

            headers["Authorization"] = f"Bearer {token}"
            try:
                resp = await client.request(method, full_url, headers=headers, **kwargs)
            except httpx.ConnectError:
                raise RuntimeError(_BACKEND_UNREACHABLE_MSG.format(url=BACKEND_URL))

    if not resp.is_success:
        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            try:
                detail = str(resp.json().get("detail", ""))[:300] or resp.text[:300]
            except Exception:
                detail = resp.text[:300]
        else:
            detail = resp.text[:300]

        friendly = _handle_status_error(resp.status_code, detail, BACKEND_URL)
        if friendly:
            raise RuntimeError(friendly)
        raise RuntimeError(f"Backend returned {resp.status_code}: {detail}")

    return resp.json()


def _format_tool_error(exc: Exception) -> str:
    """Return a tool-result-safe error string from an exception.

    Ensures the MCP client receives an actionable message rather than a raw
    exception traceback or silent failure.
    """
    msg = str(exc)
    if msg.startswith("Error:") or msg.startswith("Authentication failed") or msg.startswith("Lineage graph"):
        return msg
    return f"Error: {msg}"


# ── tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def search_provenance(
    query: str,
    file_path: str = "",
    model: str = "",
    limit: int = 10,
) -> str:
    """Search LineageLens for AI-generated code by natural language query.

    Returns matching provenance records — file path, AI model, timestamp, and a code
    snippet for each match. Use this to find where specific logic came from, which AI
    tool wrote it, and when it was inserted.

    Args:
        query: What to search for, e.g. "JWT authentication", "database migration helper"
        file_path: Restrict results to a specific file path (optional)
        model: Filter by AI model name, e.g. "claude", "gpt-4o" (optional)
        limit: Number of results to return, max 50 (default 10)
    """
    try:
        body: dict[str, Any] = {"query": query, "limit": min(max(1, limit), 50)}
        if file_path:
            body["filePath"] = file_path
        if model:
            body["model"] = model

        data = await _req("POST", SEARCH_ENDPOINT, json=body)
        if isinstance(data, str):
            return data  # friendly error from _req
        results = data.get("results", [])
        warnings = data.get("warnings", [])

        if not results:
            msg = "No matching provenance records found."
            if warnings:
                msg += "\nWarnings: " + "; ".join(warnings)
            return msg

        lines = [f"Found {data.get('count', len(results))} result(s):\n"]
        for i, r in enumerate(results, 1):
            lines.extend(_format_search_result(i, r))

        if warnings:
            lines.append("Warnings: " + "; ".join(warnings))

        return "\n".join(lines)
    except Exception as exc:
        return _format_tool_error(exc)


@mcp.tool()
async def get_record(uuid: str) -> str:
    """Fetch the full provenance record for an AI-generated code event.

    Returns all stored metadata: inserted code, model name, agent context, risk
    assessment, tool name, prompt messages, lineage node ID, and more.
    Use after search_provenance to drill into a specific result.

    Args:
        uuid: The UUID of the provenance record (from search_provenance or list_recent)
    """
    try:
        data = await _req("GET", f"/provenance/{uuid.strip()}")
        if isinstance(data, str):
            return data
        record = data.get("record", data)
        return json.dumps(record, indent=2, default=str)
    except Exception as exc:
        return _format_tool_error(exc)


@mcp.tool()
async def get_insights() -> str:
    """Get the LineageLens governance dashboard for your workspace.

    Returns a summary of all AI-generated code activity: total records, prompt capture
    rate, average risk score, high-risk and critical record counts, unique files and
    models touched, total AI lines added, agent session count, team member count,
    compliance control statuses, and the top high-risk files.

    Use this to understand overall AI code health at a glance before reviewing a PR,
    during a sprint retro, or when onboarding to an unfamiliar codebase.
    """
    try:
        data = await _req("POST", "/insights/dashboard", json={})
        if isinstance(data, str):
            return data
        # Backend returns payload with key "summary" (not "governanceSummary")
        gs = data.get("summary", {})
        controls = data.get("complianceControls", [])
        high_risk = data.get("highRiskRecords", [])

        lines = ["=== LineageLens Governance Dashboard ===\n"]
        lines.append(f"Total Records:    {gs.get('totalRecords', '—')}")
        lines.append(f"Prompt Capture:   {_pct(gs.get('promptCaptureRate'))}")
        lines.append(f"Avg Risk Score:   {_pct(gs.get('avgRiskScore'))}")
        lines.append(f"High Risk:        {gs.get('highRiskRecords', '—')}")
        lines.append(f"Critical:         {gs.get('criticalRecords', '—')}")
        lines.append(f"Unique Files:     {gs.get('uniqueFiles', '—')}")
        lines.append(f"Unique Models:    {gs.get('uniqueModels', '—')}")
        lines.append(f"AI Lines Added:   {gs.get('totalNetAddedLines', '—')}")
        lines.append(f"Agent Sessions:   {gs.get('uniqueAgentSessions', '—')}")
        lines.append(f"Team Members:     {len(data.get('memberStats', []))}")

        if controls:
            lines.append("\nCompliance Controls:")
            lines.extend(_format_compliance_controls(controls))

        if high_risk:
            lines.append(f"\nTop High-Risk Records ({len(high_risk)} shown):")
            lines.extend(_format_high_risk_records(high_risk[:8]))

        return "\n".join(lines)
    except Exception as exc:
        return _format_tool_error(exc)


@mcp.tool()
async def explain_record(uuid: str) -> str:
    """Generate a plain-English explanation of an AI-generated code provenance record.

    Uses an LLM (configured on the backend) to describe what the code does, what
    prompt or context triggered it, and which tool or session produced it. Useful for
    understanding unfamiliar AI-generated code or building audit narratives.

    Args:
        uuid: The UUID of the provenance record to explain
    """
    try:
        data = await _req("POST", "/explain", json={"uuid": uuid.strip()})
        if isinstance(data, str):
            return data
        explanation = data.get("explanation", "No explanation returned.")
        model = data.get("model", "unknown")
        source = data.get("source", "")
        return f"[Explained by {model} via {source}]\n\n{explanation}"
    except Exception as exc:
        return _format_tool_error(exc)


@mcp.tool()
async def list_recent(limit: int = 10, file_path: str = "") -> str:
    """List the most recently captured AI code insertions in your workspace.

    Returns a chronological feed of AI-generated code events — what was written, by
    which model, in which file, and when. Useful for reviewing what AI did in the last
    session, auditing a colleague's AI usage, or catching unexpected insertions.

    Args:
        limit: Number of recent records to return, max 50 (default 10)
        file_path: Restrict to a specific file path (optional)
    """
    try:
        body: dict[str, Any] = {"limit": min(max(1, limit), 50)}
        if file_path:
            body["filePath"] = file_path

        data = await _req("POST", SEARCH_ENDPOINT, json=body)
        if isinstance(data, str):
            return data
        results = data.get("results", [])

        if not results:
            return "No recent provenance records found."

        lines = [f"Recent AI captures ({data.get('count', len(results))} record(s)):\n"]
        for i, r in enumerate(results, 1):
            ts = (r.get("timestampIso") or "")[:19] or "—"
            fp = r.get("filePath") or "—"
            mdl = r.get("model") or "—"
            uid = (r.get("uuid") or "?")[:8]
            lines.append(f"[{i:02}] {ts}  {fp}")
            lines.append(f"      model={mdl}  uuid={uid}…")
            snippet = (r.get("snippet") or "").strip()
            if snippet:
                flat = snippet[:120].replace("\n", " ")
                lines.append(f"      {flat}")
            lines.append("")

        return "\n".join(lines)
    except Exception as exc:
        return _format_tool_error(exc)


@mcp.tool()
async def check_file_risk(file_path: str) -> str:
    """Check the AI risk profile for a specific file in your workspace.

    Searches for all provenance records for the given file and summarises the risk
    distribution — how many insertions were critical, high, medium, or low risk.
    Use this before committing or reviewing a file to understand its AI exposure.

    Args:
        file_path: Path of the file to check, e.g. "src/auth/middleware.ts"
    """
    try:
        data = await _req("POST", SEARCH_ENDPOINT, json={"filePath": file_path, "limit": 50})
    except Exception as exc:
        return _format_tool_error(exc)

    if isinstance(data, str):
        return data

    results = data.get("results", [])

    if not results:
        return f"No AI provenance records found for '{file_path}'."

    risk_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    models: dict[str, int] = {}
    total = len(results)

    # Fetch full records to get accurate risk assessment data
    for r in results:
        uuid = (r.get("uuid") or "").strip()
        mdl = r.get("model") or "unknown"
        models[mdl] = models.get(mdl, 0) + 1

        lvl = "unknown"
        if uuid:
            try:
                full_data = await _req("GET", f"/provenance/{uuid}")
                if isinstance(full_data, str):
                    continue  # auth/503 error, skip this record
                rec = full_data.get("record", full_data)
                ra = rec.get("riskAssessment") or {}
                risk_score = (
                    ra.get("score")
                    or rec.get("riskScore")
                    or rec.get("risk_score")
                    or 0
                )
                risk_label = ra.get("level") or ra.get("label") or ""
                if risk_label:
                    lvl = risk_label.lower()
                elif isinstance(risk_score, (int, float)):
                    score = float(risk_score)
                    if score >= 85:
                        lvl = "critical"
                    elif score >= 65:
                        lvl = "high"
                    elif score >= 35:
                        lvl = "medium"
                    else:
                        lvl = "low"
            except Exception:
                pass

        risk_counts[lvl if lvl in risk_counts else "unknown"] += 1

    lines = [f"AI risk profile for: {file_path}\n"]
    lines.append(f"Total AI insertions: {total}")
    lines.append("\nRisk breakdown:")
    for lvl, count in risk_counts.items():
        if count > 0:
            bar = "█" * count
            lines.append(f"  {lvl.upper():8} {count:3}  {bar}")
    lines.append("\nModels used:")
    for mdl, count in sorted(models.items(), key=lambda x: -x[1]):
        lines.append(f"  {mdl}: {count}")
    lines.append("\nTop records by UUID:")
    for r in results[:5]:
        uid = (r.get("uuid") or "?")[:8]
        ts = (r.get("timestampIso") or "")[:19]
        lines.append(f"  {uid}…  {ts}")

    return "\n".join(lines)


@mcp.tool()
async def usage_report(date_from: str = "", date_to: str = "") -> str:
    """Generate an AI usage report for the workspace — the CEO summary.

    Returns total AI insertions, lines added, model breakdown, top risky files,
    developer breakdown, prompt capture rate, risk distribution, and compliance
    control statuses. Results are cached on the backend for 5 minutes.

    Args:
        date_from: Start of the reporting period in ISO 8601 format, e.g. "2026-01-01T00:00:00Z" (optional)
        date_to: End of the reporting period in ISO 8601 format, e.g. "2026-05-01T00:00:00Z" (optional)
    """
    try:
        params: dict[str, str] = {}
        if date_from:
            params["dateFrom"] = date_from
        if date_to:
            params["dateTo"] = date_to

        query_string = urllib.parse.urlencode(params)
        path = f"/report/usage?{query_string}" if query_string else "/report/usage"
        data = await _req("GET", path)
        if isinstance(data, str):
            return data

        s = data.get("summary", {})
        lines = ["=== LineageLens AI Usage Report ===\n"]

        period_from = (data.get("period") or {}).get("from") or "all time"
        period_to = (data.get("period") or {}).get("to") or "now"
        lines.append(f"Period:          {period_from} → {period_to}")
        lines.append(f"Workspace:       {data.get('workspace', '—')}")
        lines.append(f"Generated:       {(data.get('generatedAt') or '')[:19]}\n")

        lines.append("── Summary ────────────────────────────")
        lines.append(f"Total AI Insertions: {s.get('totalInsertions', '—')}")
        lines.append(f"Total AI Lines:      {s.get('totalAiLines', '—')}")
        lines.append(f"Unique Files:        {s.get('uniqueFiles', '—')}")
        lines.append(f"Unique Models:       {s.get('uniqueModels', '—')}")
        lines.append(f"Agent Sessions:      {s.get('agentSessions', '—')}")
        lines.append(f"Team Members:        {s.get('teamMembers', '—')}")
        lines.append(f"Prompt Capture:      {_pct(s.get('promptCaptureRate'))}")
        lines.append(f"Avg Risk Score:      {_pct(s.get('avgRiskScore'))}")

        rd = data.get("riskDistribution", {})
        if rd:
            lines.append("\n── Risk Distribution ──────────────────")
            lines.extend(_format_risk_distribution(rd))

        model_usage = data.get("modelUsage", [])
        if model_usage:
            lines.append("\n── Model Usage ────────────────────────")
            lines.extend(_format_model_usage(model_usage))

        risky_files = data.get("topRiskyFiles", [])
        if risky_files:
            lines.append("\n── Top Risky Files ────────────────────")
            lines.extend(_format_risky_files(risky_files))

        devs = data.get("developerBreakdown", [])
        if devs:
            lines.append("\n── Developer Breakdown ────────────────")
            lines.extend(_format_developer_breakdown(devs))

        compliance = data.get("complianceStatus", [])
        if compliance:
            lines.append("\n── Compliance Controls ────────────────")
            lines.extend(_format_report_compliance(compliance))

        warnings = data.get("warnings", [])
        if warnings:
            lines.append("\nWarnings: " + "; ".join(warnings))

        return "\n".join(lines)
    except Exception as exc:
        return _format_tool_error(exc)


@mcp.tool()
async def list_workspaces() -> str:
    """List all workspaces accessible to the current user.

    Returns workspace names, IDs, member counts, and creation dates.
    Use this to understand the scope of your LineageLens deployment and
    to find workspace IDs needed for other queries.
    """
    try:
        data = await _req("GET", "/workspaces/me")
        if isinstance(data, str):
            return data

        workspaces = data if isinstance(data, list) else data.get("workspaces", [data])

        if not workspaces:
            return "No workspaces found for the current user."

        lines = [f"Workspaces ({len(workspaces)}):\n"]
        for i, ws in enumerate(workspaces, 1):
            ws_id = ws.get("id") or ws.get("workspaceId") or "?"
            name = ws.get("name") or ws.get("slug") or "?"
            members = ws.get("memberCount") or ws.get("members") or "?"
            created = (ws.get("createdAt") or "")[:10] or "?"
            lines.append(f"[{i}] {name}")
            lines.append(f"    ID:       {ws_id}")
            lines.append(f"    Members:  {members}")
            lines.append(f"    Created:  {created}")
            lines.append("")

        return "\n".join(lines)
    except Exception as exc:
        return _format_tool_error(exc)


# ── helpers ───────────────────────────────────────────────────────────────────

def _format_search_result(index: int, r: dict) -> list[str]:
    """Format a single search result record into display lines."""
    lines = []
    lines.append(f"[{index}] UUID:      {r.get('uuid', '?')}")
    lines.append(f"    File:      {r.get('filePath') or '—'}")
    lines.append(f"    Model:     {r.get('model') or '—'}")
    lines.append(f"    Timestamp: {(r.get('timestampIso') or '')[:19] or '—'}")
    score = r.get("score")
    if score is not None:
        lines.append(f"    Score:     {score:.3f}")
    snippet = (r.get("snippet") or "").strip()
    if snippet:
        indented = snippet[:300].replace("\n", "\n      ")
        lines.append(f"    Snippet:\n      {indented}")
    lines.append("")
    return lines


def _format_compliance_controls(controls: list) -> list[str]:
    """Format compliance control entries for the governance dashboard."""
    lines = []
    for c in controls:
        status_label = (c.get("status") or "?").upper().ljust(7)
        name = c.get("name") or "?"
        metric = c.get("metric") or ""
        summary = c.get("summary") or ""
        lines.append(f"  [{status_label}] {name}")
        if metric:
            lines.append(f"           metric:  {metric}")
        if summary:
            lines.append(f"           detail:  {summary}")
    return lines


def _format_high_risk_records(records: list) -> list[str]:
    """Format high-risk record entries for the governance dashboard."""
    lines = []
    for r in records:
        ra = r.get("riskAssessment") or {}
        lvl = (ra.get("level") or "?").upper().ljust(8)
        fp = r.get("filePath") or "?"
        uid = (r.get("uuid") or "?")[:8]
        lines.append(f"  [{lvl}] {fp}  ({uid}…)")
    return lines


def _format_risk_distribution(rd: dict) -> list[str]:
    """Format risk distribution breakdown for the usage report."""
    lines = []
    for lvl in ("critical", "high", "medium", "low"):
        count = rd.get(lvl, 0)
        if count:
            lines.append(f"  {lvl.upper():8} {count}")
    return lines


def _format_model_usage(model_usage: list) -> list[str]:
    """Format model usage section for the usage report."""
    lines = []
    for m in model_usage:
        lines.append(
            f"  {m.get('model', '—'):40}  {m.get('insertions', 0)} insertions"
            f"  {m.get('linesAdded', 0)} lines  risk={_pct(m.get('avgRisk'))}"
        )
    return lines


def _format_risky_files(risky_files: list) -> list[str]:
    """Format top risky files section for the usage report."""
    lines = []
    for f in risky_files:
        lvl = (f.get("riskLevel") or "?").upper()
        lines.append(f"  [{lvl:8}] {f.get('filePath', '—')}  ({f.get('insertions', 0)} insertions)")
    return lines


def _format_developer_breakdown(devs: list) -> list[str]:
    """Format developer breakdown section for the usage report."""
    lines = []
    for d in devs:
        share = f"{d.get('aiShare', 0) * 100:.1f}%"
        lines.append(
            f"  {d.get('username', '—'):20}  {d.get('insertions', 0)} insertions"
            f"  {d.get('linesAdded', 0)} lines  AI share: {share}"
        )
    return lines


def _format_report_compliance(compliance: list) -> list[str]:
    """Format compliance controls section for the usage report."""
    lines = []
    for c in compliance:
        status = (c.get("status") or "?").upper().ljust(7)
        lines.append(f"  [{status}] {c.get('control', '—')}  {c.get('metric', '')}")
    return lines


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        n = float(v)
        return f"{(n * 100 if n <= 1 else n):.1f}%"
    except (TypeError, ValueError):
        return str(v)


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
