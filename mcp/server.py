#!/usr/bin/env python3
"""LineageLens MCP Server

Exposes AI provenance tools to any MCP-capable client (Claude Code, Cursor, Continue, etc.).
Connects to a running LineageLens backend and surfaces search, record lookup,
governance insights, and plain-English explanations as MCP tools.

Environment variables:
    LINEAGELENS_BACKEND_URL   Backend base URL (default: http://localhost:8787)
    LINEAGELENS_ACCESS_TOKEN  Pre-obtained JWT access token  -- OR --
    LINEAGELENS_USERNAME      LineageLens account username
    LINEAGELENS_PASSWORD      LineageLens account password
"""
import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BACKEND_URL: str = os.environ.get("LINEAGELENS_BACKEND_URL", "http://localhost:8787").rstrip("/")
_STATIC_TOKEN: str = os.environ.get("LINEAGELENS_ACCESS_TOKEN", "").strip()
_USERNAME: str = os.environ.get("LINEAGELENS_USERNAME", "").strip()
_PASSWORD: str = os.environ.get("LINEAGELENS_PASSWORD", "").strip()

mcp = FastMCP("lineagelens")

_cached_token: str | None = None


async def _login() -> str:
    global _cached_token
    if not _USERNAME or not _PASSWORD:
        raise RuntimeError(
            "Authentication required. Set LINEAGELENS_ACCESS_TOKEN or "
            "LINEAGELENS_USERNAME + LINEAGELENS_PASSWORD environment variables."
        )
    async with httpx.AsyncClient(timeout=12.0) as client:
        resp = await client.post(
            f"{BACKEND_URL}/auth/login",
            json={"username": _USERNAME, "password": _PASSWORD},
        )
        resp.raise_for_status()
    _cached_token = resp.json()["accessToken"]
    return _cached_token


async def _get_token() -> str:
    if _STATIC_TOKEN:
        return _STATIC_TOKEN
    if _cached_token:
        return _cached_token
    return await _login()


async def _req(method: str, path: str, **kwargs: Any) -> Any:
    global _cached_token
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.request(method, f"{BACKEND_URL}{path}", headers=headers, **kwargs)

    if resp.status_code == 401 and not _STATIC_TOKEN:
        _cached_token = None
        token = await _login()
        headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.request(method, f"{BACKEND_URL}{path}", headers=headers, **kwargs)

    if not resp.is_success:
        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            detail = resp.json().get("detail", resp.text[:300])
        else:
            detail = resp.text[:300]
        raise RuntimeError(f"Backend returned {resp.status_code}: {detail}")

    return resp.json()


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
    body: dict[str, Any] = {"query": query, "limit": min(max(1, limit), 50)}
    if file_path:
        body["filePath"] = file_path
    if model:
        body["model"] = model

    data = await _req("POST", "/search", json=body)
    results = data.get("results", [])
    warnings = data.get("warnings", [])

    if not results:
        msg = "No matching provenance records found."
        if warnings:
            msg += "\nWarnings: " + "; ".join(warnings)
        return msg

    lines = [f"Found {data['count']} result(s):\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] UUID:      {r['uuid']}")
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

    if warnings:
        lines.append("Warnings: " + "; ".join(warnings))

    return "\n".join(lines)


@mcp.tool()
async def get_record(uuid: str) -> str:
    """Fetch the full provenance record for an AI-generated code event.

    Returns all stored metadata: inserted code, model name, agent context, risk
    assessment, tool name, prompt messages, lineage node ID, and more.
    Use after search_provenance to drill into a specific result.

    Args:
        uuid: The UUID of the provenance record (from search_provenance or list_recent)
    """
    data = await _req("GET", f"/provenance/{uuid.strip()}")
    record = data.get("record", data)
    return json.dumps(record, indent=2, default=str)


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
    data = await _req("POST", "/insights/dashboard", json={})
    gs = data.get("governanceSummary", {})
    controls = data.get("complianceControls", [])
    high_risk = data.get("highRiskRecords", [])

    lines = ["=== LineageLens Governance Dashboard ===\n"]
    lines.append(f"Total Records:    {gs.get('totalRecords', '—')}")
    lines.append(f"Prompt Capture:   {_pct(gs.get('promptCaptureRate'))}")
    lines.append(f"Avg Risk Score:   {_pct(gs.get('avgRiskScore'))}")
    lines.append(f"High Risk:        {gs.get('highRiskCount', '—')}")
    lines.append(f"Critical:         {gs.get('criticalCount', '—')}")
    lines.append(f"Unique Files:     {gs.get('uniqueFiles', '—')}")
    lines.append(f"Unique Models:    {gs.get('uniqueModels', '—')}")
    lines.append(f"AI Lines Added:   {gs.get('aiNetLinesAdded', '—')}")
    lines.append(f"Agent Sessions:   {gs.get('agentSessionCount', '—')}")
    lines.append(f"Team Members:     {gs.get('teamMemberCount', '—')}")

    if controls:
        lines.append("\nCompliance Controls:")
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

    if high_risk:
        lines.append(f"\nTop High-Risk Records ({len(high_risk)} shown):")
        for r in high_risk[:8]:
            ra = r.get("riskAssessment") or {}
            lvl = (ra.get("level") or "?").upper().ljust(8)
            fp = r.get("filePath") or "?"
            uid = (r.get("uuid") or "?")[:8]
            lines.append(f"  [{lvl}] {fp}  ({uid}…)")

    return "\n".join(lines)


@mcp.tool()
async def explain_record(uuid: str) -> str:
    """Generate a plain-English explanation of an AI-generated code provenance record.

    Uses an LLM (configured on the backend) to describe what the code does, what
    prompt or context triggered it, and which tool or session produced it. Useful for
    understanding unfamiliar AI-generated code or building audit narratives.

    Args:
        uuid: The UUID of the provenance record to explain
    """
    data = await _req("POST", "/explain", json={"uuid": uuid.strip()})
    explanation = data.get("explanation", "No explanation returned.")
    model = data.get("model", "unknown")
    source = data.get("source", "")
    return f"[Explained by {model} via {source}]\n\n{explanation}"


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
    body: dict[str, Any] = {"limit": min(max(1, limit), 50)}
    if file_path:
        body["filePath"] = file_path

    data = await _req("POST", "/search", json=body)
    results = data.get("results", [])

    if not results:
        return "No recent provenance records found."

    lines = [f"Recent AI captures ({data['count']} record(s)):\n"]
    for i, r in enumerate(results, 1):
        ts = (r.get("timestampIso") or "")[:19] or "—"
        fp = r.get("filePath") or "—"
        mdl = r.get("model") or "—"
        uid = r["uuid"][:8]
        lines.append(f"[{i:02}] {ts}  {fp}")
        lines.append(f"      model={mdl}  uuid={uid}…")
        snippet = (r.get("snippet") or "").strip()
        if snippet:
            flat = snippet[:120].replace("\n", " ")
            lines.append(f"      {flat}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def check_file_risk(file_path: str) -> str:
    """Check the AI risk profile for a specific file in your workspace.

    Searches for all provenance records for the given file and summarises the risk
    distribution — how many insertions were critical, high, medium, or low risk.
    Use this before committing or reviewing a file to understand its AI exposure.

    Args:
        file_path: Path of the file to check, e.g. "src/auth/middleware.ts"
    """
    data = await _req("POST", "/search", json={"filePath": file_path, "limit": 50})
    results = data.get("results", [])

    if not results:
        return f"No AI provenance records found for '{file_path}'."

    risk_counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    models: dict[str, int] = {}
    total = len(results)

    for r in results:
        record = r.get("record") or {}
        ra = record.get("riskAssessment") or {}
        lvl = (ra.get("level") or "unknown").lower()
        risk_counts[lvl if lvl in risk_counts else "unknown"] += 1
        mdl = r.get("model") or "unknown"
        models[mdl] = models.get(mdl, 0) + 1

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
    lines.append(f"\nTop records by UUID:")
    for r in results[:5]:
        uid = r["uuid"][:8]
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
    params: dict[str, str] = {}
    if date_from:
        params["dateFrom"] = date_from
    if date_to:
        params["dateTo"] = date_to

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    path = f"/report/usage?{query_string}" if query_string else "/report/usage"
    data = await _req("GET", path)

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
        for lvl in ("critical", "high", "medium", "low"):
            count = rd.get(lvl, 0)
            if count:
                lines.append(f"  {lvl.upper():8} {count}")

    model_usage = data.get("modelUsage", [])
    if model_usage:
        lines.append("\n── Model Usage ────────────────────────")
        for m in model_usage:
            lines.append(
                f"  {m.get('model', '—'):40}  {m.get('insertions', 0)} insertions"
                f"  {m.get('linesAdded', 0)} lines  risk={_pct(m.get('avgRisk'))}"
            )

    risky_files = data.get("topRiskyFiles", [])
    if risky_files:
        lines.append("\n── Top Risky Files ────────────────────")
        for f in risky_files:
            lvl = (f.get("riskLevel") or "?").upper()
            lines.append(f"  [{lvl:8}] {f.get('filePath', '—')}  ({f.get('insertions', 0)} insertions)")

    devs = data.get("developerBreakdown", [])
    if devs:
        lines.append("\n── Developer Breakdown ────────────────")
        for d in devs:
            share = f"{d.get('aiShare', 0) * 100:.1f}%"
            lines.append(
                f"  {d.get('username', '—'):20}  {d.get('insertions', 0)} insertions"
                f"  {d.get('linesAdded', 0)} lines  AI share: {share}"
            )

    compliance = data.get("complianceStatus", [])
    if compliance:
        lines.append("\n── Compliance Controls ────────────────")
        for c in compliance:
            status = (c.get("status") or "?").upper().ljust(7)
            lines.append(f"  [{status}] {c.get('control', '—')}  {c.get('metric', '')}")

    warnings = data.get("warnings", [])
    if warnings:
        lines.append("\nWarnings: " + "; ".join(warnings))

    return "\n".join(lines)


# ── helpers ───────────────────────────────────────────────────────────────────

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
