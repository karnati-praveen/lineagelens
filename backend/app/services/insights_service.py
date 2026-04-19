from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import re
from typing import Any

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProvenanceRecord
from app.schemas.provenance import SearchRequest
from app.services.provenance_service import (
    build_workspace_record_filters,
    serialize_provenance_record,
)


HIGH_RISK_THRESHOLD = 65
CRITICAL_RISK_THRESHOLD = 85
AGENT_SESSION_GAP_SECONDS = 20 * 60


async def get_insights_dashboard_payload(
    session: AsyncSession,
    search: SearchRequest,
    workspace_id: str,
) -> dict[str, Any]:
    statement = (
        select(ProvenanceRecord)
        .where(and_(*build_workspace_record_filters(search, workspace_id)))
        .order_by(desc(ProvenanceRecord.timestamp_iso))
    )

    result = await session.execute(statement)
    rows = result.scalars().all()
    records = [serialize_provenance_record(row) for row in rows]

    return build_insights_dashboard(records)


def build_insights_dashboard(records: list[dict[str, Any]]) -> dict[str, Any]:
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
        "highRiskRecords": [
            to_record_preview(item["record"], item["risk"], item["agentContext"], item["model"])
            for item in sorted(
                high_risk,
                key=lambda value: (
                    value["risk"]["score"],
                    str(value["record"].get("timestampIso", "")),
                ),
                reverse=True,
            )[:12]
        ],
        "hotspots": build_hotspots(summaries),
        "modelAnalytics": build_model_analytics(summaries),
        "riskTrends": build_risk_trends(summaries),
        "agentSessions": sessions,
        "warnings": [],
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
            evidence_values = []
            for evidence in agent_context.get("evidence") or []:
                if isinstance(evidence, dict):
                    evidence_values.append(
                        f"{str(evidence.get('field') or 'unknown')}: {str(evidence.get('value') or '')}"
                    )

            current = {
                "sessionId": native_key
                or str(agent_context.get("sessionId") or item["record"].get("uuid") or item["record"].get("id")),
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
                "records": [item["record"]],
                "evidence": evidence_values,
            }
            sessions.append(current)
            if native_key:
                latest_by_native_id[native_key] = current
            latest_by_signature[signature] = current
        else:
            current["records"].append(item["record"])
            current["endedAt"] = timestamp
            if native_key:
                latest_by_native_id[native_key] = current

    rows: list[dict[str, Any]] = []
    for session in sessions:
        files = {
            str(pick_first(record, [["file", "path"], ["filePath"]]) or "").strip()
            for record in session["records"]
            if pick_first(record, [["file", "path"], ["filePath"]])
        }
        record_count = len(session["records"])
        high_risk_count = sum(
            1 for record in session["records"] if get_risk_assessment(record)["score"] >= HIGH_RISK_THRESHOLD
        )
        prompt_captured_count = sum(
            1
            for record in session["records"]
            if str(record.get("promptStatus", "")).strip().lower() == "captured"
        )
        total_net_added_lines = sum(
            max(0, int(pick_first(record, [["insertion", "netAddedLines"], ["netAddedLines"]]) or 0))
            for record in session["records"]
        )

        rows.append(
            {
                "sessionId": session["sessionId"],
                "conversationId": session.get("conversationId"),
                "runId": session.get("runId"),
                "toolName": session["toolName"],
                "provider": session["provider"],
                "modelName": session["modelName"],
                "adapterName": session.get("adapterName"),
                "adapterConfidence": session.get("adapterConfidence"),
                "sessionKind": session["sessionKind"],
                "startedAtIso": session["startedAt"].isoformat() + "Z",
                "endedAtIso": session["endedAt"].isoformat() + "Z",
                "recordCount": record_count,
                "highRiskCount": high_risk_count,
                "promptCaptureRate": round(prompt_captured_count / max(1, record_count), 4),
                "totalNetAddedLines": total_net_added_lines,
                "files": sorted(file_path for file_path in files if file_path),
                "evidence": list(dict.fromkeys(session.get("evidence", [])))[:6],
            }
        )

    return sorted(rows, key=lambda row: row["endedAtIso"], reverse=True)[:12]


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

    if prompt_status != "captured":
        score += 24
        reasons.append("Prompt capture is missing, which reduces auditability and reviewer confidence.")
        categories.add("provenance")

    if correlation_confidence is not None and correlation_confidence < 0.4:
        score += 16
        reasons.append("Prompt-to-code correlation confidence is low.")
        categories.add("provenance")
    elif correlation_confidence is not None and correlation_confidence < 0.65:
        score += 8
        reasons.append("Prompt-to-code correlation confidence is only moderate.")
        categories.add("provenance")

    if net_added_lines >= 80:
        score += 18
        reasons.append("A large AI-generated block was introduced.")
        categories.add("reliability")
    elif net_added_lines >= 30:
        score += 10
        reasons.append("The generated block is large enough to warrant focused review.")
        categories.add("reliability")

    if contains_pattern(inserted_code, [r"api[_-]?key", r"access[_-]?token", r"private[_-]?key"]):
        score += 28
        reasons.append("The inserted block appears to contain credential-like material.")
        categories.add("security")

    if contains_pattern(
        inserted_code,
        [r"\beval\b", r"\bFunction\s*\(", r"new Function", r"\bexec\s*\(", r"\bexecSync\s*\("],
    ):
        score += 24
        reasons.append("Dynamic code execution is present in the generated block.")
        categories.add("security")

    if contains_pattern(
        inserted_code,
        [r"\bsubprocess\.", r"\bos\.system\b", r"\bchild_process\b", r"\bspawn(?:Sync)?\b"],
    ):
        score += 22
        reasons.append("Shell or process execution was introduced by the generated block.")
        categories.add("security")

    if contains_pattern(inserted_code, [r"dangerouslySetInnerHTML", r"\binnerHTML\s*="]):
        score += 20
        reasons.append("Unsafe DOM mutation patterns were introduced.")
        categories.add("security")

    if contains_pattern(
        inserted_code,
        [
            r"\bSELECT\s+.+\bFROM\b",
            r"\bINSERT\s+INTO\b",
            r"\bUPDATE\s+\w+\s+SET\b",
            r"\bDELETE\s+FROM\b",
        ],
    ):
        score += 16
        reasons.append("Raw SQL appears in the generated block.")
        categories.add("reliability")

    if contains_pattern(inserted_code, [r"\bpassword\b", r"\btoken\b", r"\bauth\b", r"\bcredential\b"]):
        score += 12
        reasons.append("Authentication or credential handling appears in the generated block.")
        categories.add("compliance")

    if contains_pattern(
        file_path,
        [r"auth", r"security", r"permission", r"oauth", r"token", r"secret", r"credential"],
    ):
        score += 14
        reasons.append("The file path suggests a security-sensitive surface.")
        categories.add("compliance")

    if contains_pattern(file_path, [r"payment", r"billing", r"invoice", r"ledger", r"finance"]):
        score += 14
        reasons.append("The file path suggests a financially sensitive surface.")
        categories.add("compliance")

    agent_context = get_agent_context(record)
    if isinstance(agent_context, dict) and agent_context.get("sessionKind") == "agentic":
        score += 6
        reasons.append("The record appears to come from an autonomous or semi-autonomous coding session.")
        categories.add("provenance")

    if not reasons:
        reasons.append("No strong governance or security risk signals were detected.")

    normalized_score = max(0, min(100, score))
    return {
        "score": normalized_score,
        "level": to_risk_level(normalized_score),
        "reasons": list(dict.fromkeys(reasons))[:5],
        "categories": sorted(categories),
    }


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

    tool_name = None
    session_kind = "unknown"
    if "cursor" in raw_blob:
        tool_name = "Cursor"
        session_kind = "agentic"
    elif "claude-code" in raw_blob or "claude code" in raw_blob:
        tool_name = "Claude Code"
        session_kind = "agentic"
    elif "aider" in raw_blob:
        tool_name = "Aider"
        session_kind = "agentic"
    elif "codex" in raw_blob:
        tool_name = "Codex CLI"
        session_kind = "agentic"
    elif "copilot" in raw_blob:
        tool_name = "GitHub Copilot"
        session_kind = "assistant"

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
        return value

    text = str(value or "").strip()
    if not text:
        return datetime.min

    normalized = text[:-1] if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min


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
        return str(value if isinstance(value, str) else value)
    except Exception:
        return ""


def contains_pattern(value: str, patterns: list[str]) -> bool:
    import re

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
