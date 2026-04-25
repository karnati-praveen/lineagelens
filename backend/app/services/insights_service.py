from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProvenanceRecord, UserAccount
from app.schemas.provenance import SearchRequest
from app.services.provenance_service import (
    build_workspace_record_filters,
    serialize_provenance_record,
)
from app.services.team_service import build_team_member_stats


HIGH_RISK_THRESHOLD = 65
CRITICAL_RISK_THRESHOLD = 85
AGENT_SESSION_GAP_SECONDS = 20 * 60
MAX_DASHBOARD_RECORDS = 2000


async def get_insights_dashboard_payload(
    session: AsyncSession,
    search: SearchRequest,
    workspace_id: str,
) -> dict[str, Any]:
    statement = (
        select(ProvenanceRecord)
        .where(and_(*build_workspace_record_filters(search, workspace_id)))
        .order_by(desc(ProvenanceRecord.timestamp_iso))
        .limit(MAX_DASHBOARD_RECORDS + 1)
    )

    result = await session.execute(statement)
    rows = result.scalars().all()
    truncated = len(rows) > MAX_DASHBOARD_RECORDS
    rows = rows[:MAX_DASHBOARD_RECORDS]
    records = [serialize_provenance_record(row) for row in rows]

    extra_warnings: list[str] = []
    if truncated:
        extra_warnings.append(
            f"Dashboard analysis is limited to the {MAX_DASHBOARD_RECORDS} most recent records; "
            "apply date or file filters to narrow results."
        )

    member_stats = await build_member_stats(session, workspace_id)
    payload = build_insights_dashboard(records, extra_warnings=extra_warnings)
    payload["memberStats"] = [member.model_dump(by_alias=True) for member in member_stats]
    return payload


async def build_member_stats(session: AsyncSession, workspace_id: str) -> list[Any]:
    users_stmt = (
        select(UserAccount)
        .where(UserAccount.workspace_id == workspace_id, UserAccount.is_active.is_(True))
        .order_by(UserAccount.created_at)
    )
    users_result = await session.execute(users_stmt)
    users = users_result.scalars().all()

    if not users:
        return []

    counts_stmt = (
        select(ProvenanceRecord.user_id, func.count(ProvenanceRecord.id).label("cnt"))
        .where(ProvenanceRecord.workspace_id == workspace_id)
        .group_by(ProvenanceRecord.user_id)
    )
    counts_result = await session.execute(counts_stmt)
    record_counts: dict[str, int] = {
        str(row.user_id): row.cnt for row in counts_result if row.user_id is not None
    }

    return build_team_member_stats(users, record_counts)


def _top_high_risk_previews(high_risk: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        high_risk,
        key=lambda item: (item["risk"]["score"], str(item["record"].get("timestampIso", ""))),
        reverse=True,
    )
    return [
        to_record_preview(item["record"], item["risk"], item["agentContext"], item["model"])
        for item in ordered[:12]
    ]


def build_insights_dashboard(
    records: list[dict[str, Any]],
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []

    for record in records:
        risk = get_risk_assessment(record)
        agent_context = get_agent_context(record)
        model_name = normalize_model_name(pick_first(record, [["prompt", "modelName"], ["model"], ["modelName"]]))

        summaries.append(
            {
                "record": record,
                "risk": risk,
                "agentContext": agent_context,
                "model": model_name,
            }
        )

    total_records = len(summaries)
    prompt_captured_records = sum(
        1 for item in summaries if str(item["record"].get("promptStatus", "")).strip().lower() == "captured"
    )
    total_risk = sum(item["risk"]["score"] for item in summaries)
    high_risk = [item for item in summaries if item["risk"]["score"] >= HIGH_RISK_THRESHOLD]
    critical = [item for item in summaries if item["risk"]["score"] >= CRITICAL_RISK_THRESHOLD]
    unique_files = {
        str(pick_first(item["record"], [["file", "path"], ["filePath"]]) or "").strip()
        for item in summaries
        if pick_first(item["record"], [["file", "path"], ["filePath"]])
    }
    unique_models = {
        item["model"] for item in summaries if isinstance(item["model"], str) and item["model"].strip()
    }
    prompt_capture_rate = round(prompt_captured_records / total_records, 4) if total_records else 0.0
    avg_risk = round(total_risk / total_records, 2) if total_records else 0.0
    sessions = build_agent_sessions(summaries)
    agentic_records = sum(
        1 for item in summaries if (item["agentContext"] or {}).get("sessionKind") == "agentic"
    )
    total_net_added_lines = sum(
        max(0, int(pick_first(item["record"], [["insertion", "netAddedLines"], ["netAddedLines"]]) or 0))
        for item in summaries
    )

    return {
        "mode": "backend",
        "generatedAtIso": datetime.now(tz=UTC).isoformat(),
        "summary": {
            "totalRecords": total_records,
            "promptCapturedRecords": prompt_captured_records,
            "promptCaptureRate": prompt_capture_rate,
            "avgRiskScore": avg_risk,
            "highRiskRecords": len(high_risk),
            "criticalRecords": len(critical),
            "uniqueFiles": len(unique_files),
            "uniqueModels": len(unique_models),
            "uniqueAgentSessions": len(sessions),
            "agenticRecords": agentic_records,
            "totalNetAddedLines": total_net_added_lines,
        },
        "complianceControls": build_compliance_controls(
            total_records=total_records,
            prompt_capture_rate=prompt_capture_rate,
            avg_risk_score=avg_risk,
            high_risk_records=len(high_risk),
            critical_records=len(critical),
            unique_agent_sessions=len(sessions),
            agentic_records=agentic_records,
            average_correlation_confidence=average_correlation_confidence(
                [item["record"] for item in summaries]
            ),
        ),
        "highRiskRecords": _top_high_risk_previews(high_risk),
        "hotspots": build_hotspots(summaries),
        "modelAnalytics": build_model_analytics(summaries),
        "riskTrends": build_risk_trends(summaries),
        "agentSessions": sessions,
        "warnings": list(extra_warnings or []),
    }


def build_compliance_controls(
    *,
    total_records: int,
    prompt_capture_rate: float,
    avg_risk_score: float,
    high_risk_records: int,
    critical_records: int,
    unique_agent_sessions: int,
    agentic_records: int,
    average_correlation_confidence: float,
) -> list[dict[str, Any]]:
    high_risk_ratio = (high_risk_records / total_records) if total_records else 0.0

    return [
        control(
            "prompt-capture",
            "Prompt Capture Coverage",
            "pass" if prompt_capture_rate >= 0.8 else "warning" if prompt_capture_rate >= 0.5 else "fail",
            format_percent(prompt_capture_rate),
            "How consistently the system retained auditable prompt evidence.",
        ),
        control(
            "risk-density",
            "High-Risk Density",
            "pass" if high_risk_ratio <= 0.1 else "warning" if high_risk_ratio <= 0.25 else "fail",
            format_percent(high_risk_ratio),
            "Share of AI-generated records currently assessed as high risk.",
        ),
        control(
            "critical-records",
            "Critical Findings",
            "pass" if critical_records == 0 else "warning" if critical_records <= 3 else "fail",
            str(critical_records),
            "Count of AI-generated records that should be escalated immediately.",
        ),
        control(
            "correlation-quality",
            "Correlation Confidence",
            "pass"
            if average_correlation_confidence >= 0.7
            else "warning"
            if average_correlation_confidence >= 0.5
            else "fail",
            format_percent(average_correlation_confidence),
            "Average confidence that prompt/response evidence matches generated code.",
        ),
        control(
            "agent-session-attribution",
            "Agent Session Attribution",
            "pass" if agentic_records > 0 and unique_agent_sessions > 0 else "warning",
            str(unique_agent_sessions),
            "Whether autonomous coding activity can be grouped into reviewable sessions.",
        ),
        control(
            "overall-risk",
            "Average Governance Risk",
            "pass" if avg_risk_score < 35 else "warning" if avg_risk_score < 60 else "fail",
            str(avg_risk_score),
            "Heuristic governance risk score across all filtered provenance records.",
        ),
    ]


def build_hotspots(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "recordCount": 0,
            "highRiskCount": 0,
            "riskScoreTotal": 0,
            "latestTimestampIso": None,
        }
    )

    for item in summaries:
        file_path = str(pick_first(item["record"], [["file", "path"], ["filePath"]]) or "(unknown file)")
        grouped[file_path]["recordCount"] += 1
        grouped[file_path]["riskScoreTotal"] += item["risk"]["score"]
        if item["risk"]["score"] >= HIGH_RISK_THRESHOLD:
            grouped[file_path]["highRiskCount"] += 1

        timestamp = str(item["record"].get("timestampIso") or "")
        if not grouped[file_path]["latestTimestampIso"] or timestamp > grouped[file_path]["latestTimestampIso"]:
            grouped[file_path]["latestTimestampIso"] = timestamp

    rows = []
    for file_path, value in grouped.items():
        rows.append(
            {
                "filePath": file_path,
                "recordCount": value["recordCount"],
                "highRiskCount": value["highRiskCount"],
                "avgRiskScore": round(value["riskScoreTotal"] / max(1, value["recordCount"]), 2),
                "latestTimestampIso": value["latestTimestampIso"],
            }
        )

    return sorted(
        rows,
        key=lambda row: (row["highRiskCount"], row["avgRiskScore"], row["recordCount"]),
        reverse=True,
    )[:12]


def build_model_analytics(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "recordCount": 0,
            "promptCaptured": 0,
            "riskScoreTotal": 0,
            "highRiskCount": 0,
        }
    )

    for item in summaries:
        model = item["model"] or "unknown"
        grouped[model]["recordCount"] += 1
        grouped[model]["riskScoreTotal"] += item["risk"]["score"]
        if str(item["record"].get("promptStatus", "")).strip().lower() == "captured":
            grouped[model]["promptCaptured"] += 1
        if item["risk"]["score"] >= HIGH_RISK_THRESHOLD:
            grouped[model]["highRiskCount"] += 1

    rows = []
    for model, value in grouped.items():
        rows.append(
            {
                "model": model,
                "recordCount": value["recordCount"],
                "promptCaptureRate": round(
                    value["promptCaptured"] / max(1, value["recordCount"]), 4
                ),
                "avgRiskScore": round(
                    value["riskScoreTotal"] / max(1, value["recordCount"]), 2
                ),
                "highRiskCount": value["highRiskCount"],
            }
        )

    return sorted(rows, key=lambda row: (row["recordCount"], row["avgRiskScore"]), reverse=True)[
        :12
    ]


def build_risk_trends(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "recordCount": 0,
            "highRiskCount": 0,
            "promptCaptured": 0,
            "riskScoreTotal": 0,
        }
    )

    for item in summaries:
        bucket = str(item["record"].get("timestampIso") or "")[:10] or "unknown"
        buckets[bucket]["recordCount"] += 1
        buckets[bucket]["riskScoreTotal"] += item["risk"]["score"]
        if item["risk"]["score"] >= HIGH_RISK_THRESHOLD:
            buckets[bucket]["highRiskCount"] += 1
        if str(item["record"].get("promptStatus", "")).strip().lower() == "captured":
            buckets[bucket]["promptCaptured"] += 1

    rows = []
    for bucket, value in sorted(buckets.items())[-10:]:
        rows.append(
            {
                "bucketLabel": bucket,
                "recordCount": value["recordCount"],
                "highRiskCount": value["highRiskCount"],
                "avgRiskScore": round(
                    value["riskScoreTotal"] / max(1, value["recordCount"]), 2
                ),
                "promptCaptureRate": round(
                    value["promptCaptured"] / max(1, value["recordCount"]), 4
                ),
            }
        )

    return rows


def build_agent_sessions(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        [item for item in summaries if item["agentContext"]],
        key=lambda item: parse_timestamp(item["record"].get("timestampIso")),
    )

    sessions: list[dict[str, Any]] = []
    latest_by_signature: dict[str, dict[str, Any]] = {}
    latest_by_native_id: dict[str, dict[str, Any]] = {}

    for item in ordered:
        agent_context = item["agentContext"]
        if not isinstance(agent_context, dict):
            continue

        native_key = get_native_session_key(agent_context)
        signature = native_key or str(agent_context.get("sessionSignature") or "unknown")
        timestamp = parse_timestamp(item["record"].get("timestampIso"))
        current = latest_by_native_id.get(native_key) if native_key else None
        if current is None:
            current = latest_by_signature.get(signature)

        if not current or (timestamp - current["endedAt"]).total_seconds() > AGENT_SESSION_GAP_SECONDS:
            current = _start_agent_session(native_key, agent_context, item, timestamp)
            sessions.append(current)
            if native_key:
                latest_by_native_id[native_key] = current
            latest_by_signature[signature] = current
        else:
            current["records"].append((item["record"], item["risk"]["score"]))
            current["endedAt"] = timestamp
            if native_key:
                latest_by_native_id[native_key] = current

    rows = [_serialize_agent_session_row(session) for session in sessions]
    return sorted(rows, key=lambda row: row["endedAtIso"], reverse=True)[:12]


def _start_agent_session(
    native_key: str | None,
    agent_context: dict[str, Any],
    item: dict[str, Any],
    timestamp: Any,
) -> dict[str, Any]:
    evidence_values = [
        f"{str(evidence.get('field') or 'unknown')}: {str(evidence.get('value') or '')}"
        for evidence in (agent_context.get("evidence") or [])
        if isinstance(evidence, dict)
    ]
    return {
        "sessionId": native_key or str(
            agent_context.get("sessionId") or item["record"].get("uuid") or item["record"].get("id")
        ),
        "conversationId": agent_context.get("conversationId"),
        "runId": agent_context.get("runId"),
        "toolName": agent_context.get("toolName"),
        "provider": agent_context.get("provider"),
        "modelName": item["model"] or agent_context.get("modelName"),
        "adapterName": agent_context.get("adapterName"),
        "adapterConfidence": to_number(agent_context.get("confidence")),
        "sessionKind": agent_context.get("sessionKind") or "unknown",
        "startedAt": timestamp,
        "endedAt": timestamp,
        "records": [(item["record"], item["risk"]["score"])],
        "evidence": evidence_values,
    }


def _serialize_agent_session_row(session: dict[str, Any]) -> dict[str, Any]:
    record_count = len(session["records"])
    files = {
        str(pick_first(record, [["file", "path"], ["filePath"]]) or "").strip()
        for record, _ in session["records"]
        if pick_first(record, [["file", "path"], ["filePath"]])
    }
    high_risk_count = sum(1 for _, s in session["records"] if s >= HIGH_RISK_THRESHOLD)
    prompt_captured = sum(
        1 for record, _ in session["records"]
        if str(record.get("promptStatus", "")).strip().lower() == "captured"
    )
    net_lines = sum(
        max(0, int(pick_first(record, [["insertion", "netAddedLines"], ["netAddedLines"]]) or 0))
        for record, _ in session["records"]
    )
    return {
        "sessionId": session["sessionId"],
        "conversationId": session.get("conversationId"),
        "runId": session.get("runId"),
        "toolName": session["toolName"],
        "provider": session["provider"],
        "modelName": session["modelName"],
        "adapterName": session.get("adapterName"),
        "adapterConfidence": session.get("adapterConfidence"),
        "sessionKind": session["sessionKind"],
        "startedAtIso": session["startedAt"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "endedAtIso": session["endedAt"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "recordCount": record_count,
        "highRiskCount": high_risk_count,
        "promptCaptureRate": round(prompt_captured / max(1, record_count), 4),
        "totalNetAddedLines": net_lines,
        "files": sorted(fp for fp in files if fp),
        "evidence": list(dict.fromkeys(session.get("evidence", [])))[:6],
    }


def to_record_preview(
    record: dict[str, Any],
    risk: dict[str, Any],
    agent_context: dict[str, Any] | None,
    model_name: str,
) -> dict[str, Any]:
    return {
        "uuid": str(record.get("uuid") or record.get("id") or ""),
        "filePath": str(pick_first(record, [["file", "path"], ["filePath"]]) or ""),
        "timestampIso": str(record.get("timestampIso") or ""),
        "model": model_name or None,
        "promptStatus": str(record.get("promptStatus") or "not-captured"),
        "riskScore": int(risk["score"]),
        "riskLevel": str(risk["level"]),
        "summary": str((risk.get("reasons") or ["No strong risk signals detected."])[0]),
        "toolName": agent_context.get("toolName") if agent_context else None,
        "provider": agent_context.get("provider") if agent_context else None,
        "adapterName": agent_context.get("adapterName") if agent_context else None,
        "adapterConfidence": agent_context.get("confidence") if agent_context else None,
        "captureStatus": record.get("correlation", {}).get("captureStatus")
        if isinstance(record.get("correlation"), dict)
        else pick_first(record, [["metadata", "captureStatus"]]),
    }


def get_risk_assessment(record: dict[str, Any]) -> dict[str, Any]:
    stored = pick_first(record, [["metadata", "riskAssessment"]])
    if isinstance(stored, dict) and isinstance(stored.get("score"), (int, float)):
        return {
            "score": int(stored.get("score") or 0),
            "level": stored.get("level") or "low",
            "reasons": stored.get("reasons") or ["Stored risk assessment available."],
        }

    score, reasons, categories = _compute_heuristic_risk(record)

    if not reasons:
        reasons.append("No strong governance or security risk signals were detected.")

    normalized_score = max(0, min(100, score))
    return {
        "score": normalized_score,
        "level": to_risk_level(normalized_score),
        "reasons": list(dict.fromkeys(reasons))[:5],
        "categories": sorted(categories),
    }


def _compute_heuristic_risk(
    record: dict[str, Any],
) -> tuple[int, list[str], set[str]]:
    score = 12
    reasons: list[str] = []
    categories: set[str] = set()

    prompt_status = str(record.get("promptStatus") or "").strip().lower()
    inserted_code = str(
        pick_first(record, [["insertion", "extractedInsertedCodeBlock"], ["insertedText"]]) or ""
    )
    file_path = str(pick_first(record, [["file", "path"], ["filePath"]]) or "")
    net_added_lines = int(pick_first(record, [["insertion", "netAddedLines"], ["netAddedLines"]]) or 0)
    correlation_confidence = to_number(pick_first(record, [["metadata", "correlationConfidence"]]))

    _apply_prompt_capture_signal(prompt_status, score, reasons, categories)
    score = _apply_prompt_capture_score(prompt_status, score)
    _apply_correlation_signal(correlation_confidence, reasons, categories)
    score = _apply_correlation_score(correlation_confidence, score)
    _apply_size_signal(net_added_lines, reasons, categories)
    score = _apply_size_score(net_added_lines, score)
    score = _apply_pattern_rules(inserted_code, file_path, reasons, categories, score)

    agent_context = get_agent_context(record)
    if isinstance(agent_context, dict) and agent_context.get("sessionKind") == "agentic":
        score += 6
        reasons.append("The record appears to come from an autonomous or semi-autonomous coding session.")
        categories.add("provenance")

    return score, reasons, categories


def _apply_prompt_capture_signal(
    prompt_status: str, score: int, reasons: list[str], categories: set[str]
) -> None:
    if prompt_status != "captured":
        reasons.append("Prompt capture is missing, which reduces auditability and reviewer confidence.")
        categories.add("provenance")


def _apply_prompt_capture_score(prompt_status: str, score: int) -> int:
    return score + 24 if prompt_status != "captured" else score


def _apply_correlation_signal(
    confidence: float | None, reasons: list[str], categories: set[str]
) -> None:
    if confidence is not None and confidence < 0.4:
        reasons.append("Prompt-to-code correlation confidence is low.")
        categories.add("provenance")
    elif confidence is not None and confidence < 0.65:
        reasons.append("Prompt-to-code correlation confidence is only moderate.")
        categories.add("provenance")


def _apply_correlation_score(confidence: float | None, score: int) -> int:
    if confidence is not None and confidence < 0.4:
        return score + 16
    if confidence is not None and confidence < 0.65:
        return score + 8
    return score


def _apply_size_signal(net_added_lines: int, reasons: list[str], categories: set[str]) -> None:
    if net_added_lines >= 80:
        reasons.append("A large AI-generated block was introduced.")
        categories.add("reliability")
    elif net_added_lines >= 30:
        reasons.append("The generated block is large enough to warrant focused review.")
        categories.add("reliability")


def _apply_size_score(net_added_lines: int, score: int) -> int:
    if net_added_lines >= 80:
        return score + 18
    if net_added_lines >= 30:
        return score + 10
    return score


_CODE_PATTERN_RULES: list[tuple[list[str], int, str, str]] = [
    ([r"api[_-]?key", r"access[_-]?token", r"private[_-]?key"], 28,
     "The inserted block appears to contain credential-like material.", "security"),
    ([r"\beval\b", r"\bFunction\s*\(", r"new Function", r"\bexec\s*\(", r"\bexecSync\s*\("], 24,
     "Dynamic code execution is present in the generated block.", "security"),
    ([r"\bsubprocess\.", r"\bos\.system\b", r"\bchild_process\b", r"\bspawn(?:Sync)?\b"], 22,
     "Shell or process execution was introduced by the generated block.", "security"),
    ([r"dangerouslySetInnerHTML", r"\binnerHTML\s*="], 20,
     "Unsafe DOM mutation patterns were introduced.", "security"),
    ([r"\bSELECT\s+.+\bFROM\b", r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b"], 16,
     "Raw SQL appears in the generated block.", "reliability"),
    ([r"\bpassword\b", r"\btoken\b", r"\bauth\b", r"\bcredential\b"], 12,
     "Authentication or credential handling appears in the generated block.", "compliance"),
]

_FILE_PATTERN_RULES: list[tuple[list[str], int, str, str]] = [
    ([r"auth", r"security", r"permission", r"oauth", r"token", r"secret", r"credential"], 14,
     "The file path suggests a security-sensitive surface.", "compliance"),
    ([r"payment", r"billing", r"invoice", r"ledger", r"finance"], 14,
     "The file path suggests a financially sensitive surface.", "compliance"),
]


def _apply_pattern_rules(
    inserted_code: str,
    file_path: str,
    reasons: list[str],
    categories: set[str],
    score: int,
) -> int:
    for patterns, delta, reason, category in _CODE_PATTERN_RULES:
        if contains_pattern(inserted_code, patterns):
            score += delta
            reasons.append(reason)
            categories.add(category)
    for patterns, delta, reason, category in _FILE_PATTERN_RULES:
        if contains_pattern(file_path, patterns):
            score += delta
            reasons.append(reason)
            categories.add(category)
    return score


_TOOL_DETECTION_RULES: list[tuple[str, str, str]] = [
    ("cursor", "Cursor", "agentic"),
    ("claude-code", "Claude Code", "agentic"),
    ("claude code", "Claude Code", "agentic"),
    ("aider", "Aider", "agentic"),
    ("codex", "Codex CLI", "agentic"),
    ("copilot", "GitHub Copilot", "assistant"),
]


def _detect_tool_from_blob(raw_blob: str) -> tuple[str | None, str]:
    for keyword, tool_name, session_kind in _TOOL_DETECTION_RULES:
        if keyword in raw_blob:
            return tool_name, session_kind
    return None, "unknown"


def get_agent_context(record: dict[str, Any]) -> dict[str, Any] | None:
    stored = pick_first(record, [["metadata", "agentContext"]])
    if isinstance(stored, dict):
        return normalize_agent_context(stored, record)

    correlation = record.get("correlation")
    if not isinstance(correlation, dict) or str(correlation.get("promptStatus") or "") != "captured":
        return None

    if str(correlation.get("captureStatus") or "full") != "full":
        return normalize_agent_context(
            {
                "toolName": None,
                "provider": None,
                "sessionId": None,
                "conversationId": None,
                "runId": None,
                "modelName": pick_first(record, [["prompt", "modelName"], ["model"], ["modelName"]]),
                "userAgent": find_header_value(correlation.get("requestHeaders"), "user-agent"),
                "workspaceHint": None,
                "operationType": "unknown",
                "confidence": 0.0,
                "evidence": [],
                "adapterName": "proxy-partial",
                "matchSource": "heuristic",
                "sessionKind": "unknown",
                "host": str(correlation.get("targetHost") or "").strip().lower() or None,
                "sessionSignature": str(
                    correlation.get("requestUuid") or correlation.get("proxyRequestTimestampIso") or "partial"
                ),
                "detectedAtIso": str(record.get("timestampIso") or ""),
            },
            record,
        )

    model_name = normalize_model_name(
        pick_first(record, [["prompt", "modelName"], ["model"], ["modelName"]])
    )
    target_host = str(correlation.get("targetHost") or "").strip().lower() or None
    headers = correlation.get("requestHeaders")
    user_agent = (
        find_header_value(headers, "user-agent")
        or find_header_value(headers, "x-client-name")
        or ""
    )
    raw_blob = "\n".join(
        [
            target_host or "",
            user_agent,
            model_name,
            safe_serialize(correlation.get("parameters")),
            safe_serialize(correlation.get("fullPromptMessages")),
        ]
    ).lower()

    tool_name, session_kind = _detect_tool_from_blob(raw_blob)
    provider = infer_provider(target_host, model_name, raw_blob)
    if session_kind == "unknown" and provider:
        session_kind = "assistant"

    if not tool_name and not provider and not model_name:
        return None

    operation_type = classify_operation_type(
        inserted_text=str(
            pick_first(
                record,
                [
                    ["insertion", "extractedInsertedCodeBlock"],
                    ["insertedText"],
                    ["inserted_code"],
                ],
            )
            or correlation.get("rawModelResponse")
            or ""
        ),
        prompt_blob=raw_blob,
        model_name=model_name,
    )

    return normalize_agent_context(
        {
        "toolName": tool_name,
        "provider": provider,
        "sessionId": find_header_value(headers, "x-request-id")
        or str(correlation.get("requestUuid") or correlation.get("proxyRequestTimestampIso") or ""),
        "conversationId": None,
        "runId": None,
        "workspaceHint": None,
        "operationType": operation_type,
        "confidence": 0.55 if tool_name else 0.42 if provider else 0.3,
        "evidence": [
            {
                "source": "heuristic",
                "field": "rawContextBlob",
                "value": raw_blob[:240],
                "weight": 0.2,
            }
        ],
        "adapterName": "legacy-heuristic",
        "matchSource": "heuristic",
        "sessionKind": session_kind,
        "host": target_host,
        "userAgent": user_agent or None,
        "modelName": model_name or None,
        "sessionSignature": "|".join(
            [
                tool_name or "unknown-tool",
                provider or "unknown-provider",
                model_name or "unknown-model",
                session_kind,
            ]
        ),
        "detectedAtIso": str(record.get("timestampIso") or ""),
        },
        record,
    )


def average_correlation_confidence(records: list[dict[str, Any]]) -> float:
    values = [
        to_number(pick_first(record, [["metadata", "correlationConfidence"]]))
        for record in records
    ]
    numbers = [value for value in values if isinstance(value, (int, float))]
    if not numbers:
        return 0.0

    return round(sum(float(value) for value in numbers) / len(numbers), 4)


def infer_provider(target_host: str | None, model_name: str, raw_blob: str) -> str | None:
    if (target_host and "openai.com" in target_host) or "gpt" in model_name:
        return "OpenAI"
    if (target_host and "anthropic.com" in target_host) or "claude" in model_name:
        return "Anthropic"
    if (target_host and "githubcopilot" in target_host) or "copilot" in raw_blob:
        return "GitHub"
    if target_host and "openrouter.ai" in target_host:
        return "OpenRouter"

    return None


def classify_operation_type(inserted_text: str, prompt_blob: str, model_name: str) -> str:
    text = "\n".join([inserted_text or "", prompt_blob or "", model_name or ""]).lower()

    if contains_pattern(
        text,
        [
            r"multi[-\s]?file",
            r"multiple files",
            r"apply_patch",
            r"across multiple files",
            r"across files",
            r"many files",
            r"batch edit",
            r"workspace run",
        ],
    ) or count_distinct_file_paths(text) > 1:
        return "multi-file-run"

    if contains_pattern(
        text,
        [
            r"test\s*[- ]?\s*fix",
            r"fix .*tests?",
            r"failing tests",
            r"pytest",
            r"jest",
            r"vitest",
            r"unit test",
        ],
    ):
        return "test-fix"

    if contains_pattern(text, [r"refactor", r"cleanup", r"rewrite", r"simplify", r"moderni[sz]e"]):
        return "refactor"

    if contains_pattern(text, [r"explain", r"why does", r"what does this do", r"summar[iy][sz]e"]):
        return "explain"

    if contains_pattern(text, [r"chat", r"conversation", r"prompt", r"reply"]):
        return "chat"

    return "edit"


def count_distinct_file_paths(text: str) -> int:
    matches = re.findall(r"(?:[A-Za-z]:)?[\\/][^\s'\"`]+?\.[a-z0-9]+", text, flags=re.IGNORECASE)
    return len({match.lower() for match in matches})


def get_native_session_key(agent_context: dict[str, Any]) -> str | None:
    for key in ("sessionId", "runId", "conversationId"):
        value = agent_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def normalize_agent_context(value: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    tool_name = value.get("toolName") if isinstance(value.get("toolName"), str) else None
    provider = value.get("provider") if isinstance(value.get("provider"), str) else None
    session_id = value.get("sessionId") if isinstance(value.get("sessionId"), str) else None
    conversation_id = value.get("conversationId") if isinstance(value.get("conversationId"), str) else None
    run_id = value.get("runId") if isinstance(value.get("runId"), str) else None
    workspace_hint = value.get("workspaceHint") if isinstance(value.get("workspaceHint"), str) else None
    operation_type = (
        value.get("operationType")
        if value.get("operationType") in {"edit", "refactor", "test-fix", "explain", "multi-file-run", "chat", "unknown"}
        else "unknown"
    )
    confidence = to_number(value.get("confidence")) or 0.0
    evidence = value.get("evidence") if isinstance(value.get("evidence"), list) else []
    adapter_name = value.get("adapterName") if isinstance(value.get("adapterName"), str) else None
    match_source = "adapter" if value.get("matchSource") == "adapter" else "heuristic"
    session_kind = value.get("sessionKind") if value.get("sessionKind") in {"agentic", "assistant", "unknown"} else "unknown"
    host = value.get("host") if isinstance(value.get("host"), str) else None
    user_agent = value.get("userAgent") if isinstance(value.get("userAgent"), str) else None
    model_name = normalize_model_name(value.get("modelName"))
    session_signature = str(value.get("sessionSignature") or "").strip()
    if not session_signature:
        session_signature = "|".join(
            [
                tool_name or "unknown-tool",
                provider or "unknown-provider",
                model_name or "unknown-model",
                session_kind,
                session_id or conversation_id or run_id or "unknown-session",
            ]
        )

    return {
        "toolName": tool_name,
        "provider": provider,
        "sessionId": session_id,
        "conversationId": conversation_id,
        "runId": run_id,
        "workspaceHint": workspace_hint,
        "operationType": operation_type,
        "confidence": round(float(confidence), 4),
        "evidence": evidence,
        "adapterName": adapter_name,
        "matchSource": match_source,
        "sessionKind": session_kind,
        "host": host,
        "userAgent": user_agent,
        "modelName": model_name or None,
        "sessionSignature": session_signature,
        "detectedAtIso": str(value.get("detectedAtIso") or record.get("timestampIso") or ""),
    }


def normalize_model_name(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return safe_serialize(value)


def find_header_value(headers: Any, key: str) -> str | None:
    if not isinstance(headers, dict):
        return None

    for header_key, header_value in headers.items():
        if str(header_key).strip().lower() != key.lower():
            continue

        if isinstance(header_value, str):
            return header_value.strip() or None

        if isinstance(header_value, list) and header_value:
            return str(header_value[0]).strip() or None

    return None


def control(
    control_id: str,
    title: str,
    status: str,
    metric: str,
    summary: str,
) -> dict[str, Any]:
    return {
        "id": control_id,
        "title": title,
        "status": status,
        "metric": metric,
        "summary": summary,
    }


def format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    text = str(value or "").strip()
    if not text:
        return datetime(1, 1, 1, tzinfo=UTC)

    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        return datetime(1, 1, 1, tzinfo=UTC)


def to_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def pick_first(source: dict[str, Any], path_candidates: list[list[str]]) -> Any:
    for path in path_candidates:
        value = get_at_path(source, path)
        if value is not None:
            return value
    return None


def get_at_path(source: Any, path: list[str]) -> Any:
    cursor: Any = source
    for segment in path:
        if not isinstance(cursor, dict) or segment not in cursor:
            return None
        cursor = cursor[segment]
    return cursor


def safe_serialize(value: Any) -> str:
    try:
        return value if isinstance(value, str) else str(value)
    except Exception:
        return ""


def contains_pattern(value: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


def to_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None

    return None


def to_risk_level(score: int) -> str:
    if score >= CRITICAL_RISK_THRESHOLD:
        return "critical"
    if score >= HIGH_RISK_THRESHOLD:
        return "high"
    if score >= 35:
        return "medium"
    return "low"
