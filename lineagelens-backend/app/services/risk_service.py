from __future__ import annotations

# Single source of truth for all risk scoring logic.
#
# Two public functions:
#   compute_risk_score       — ingest path (raw fields, all tiers)
#   compute_risk_from_record — insights path (serialized record dict, Plus/Max only)
#
# insights_service._compute_heuristic_risk delegates to compute_risk_from_record.
# The agentic-session signal requires get_agent_context() (defined in
# insights_service), so callers pass is_agentic=True rather than re-implementing
# that detection here.

import re
from typing import Any


# Detection patterns for dangerous constructs in AI-generated code.
# These are regex strings used to scan insertions — they are never executed.
# 4-tuple: (patterns, score_delta, reason_text, category)
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


def _contains_pattern(value: str, patterns: list[str]) -> bool:
    return any(re.search(p, value, flags=re.IGNORECASE) for p in patterns)


def _pick_first(source: dict[str, Any], path_candidates: list[list[str]]) -> Any:
    """Return the first non-None value found at any of the given key paths."""
    for path in path_candidates:
        node: Any = source
        for key in path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if node is not None:
            return node
    return None


def compute_risk_score(
    inserted_code: str,
    prompt_messages: object | None = None,
    model_name: str | None = None,
    file_path: str | None = None,
) -> tuple[int, list[str]]:
    """Ingest-time risk score from raw field values. Returns (score 0-100, reasons).

    Called for every tier including Lite. Does not include insights-only signals
    (correlation confidence, agentic session) because those are unavailable at
    ingest time.
    """
    score = 12
    reason_set: set[str] = set()

    if prompt_messages is None:
        score += 24
        reason_set.add("Prompt capture is missing, which reduces auditability and reviewer confidence.")

    code_str = inserted_code or ""
    net_lines = max(1, code_str.count("\n") + 1) if code_str.strip() else 0

    if net_lines >= 80:
        score += 18
        reason_set.add("A large AI-generated block was introduced.")
    elif net_lines >= 30:
        score += 10
        reason_set.add("The generated block is large enough to warrant focused review.")

    for patterns, delta, reason, _category in _CODE_PATTERN_RULES:
        if _contains_pattern(code_str, patterns):
            score += delta
            reason_set.add(reason)

    fp = file_path or ""
    for patterns, delta, reason, _category in _FILE_PATTERN_RULES:
        if _contains_pattern(fp, patterns):
            score += delta
            reason_set.add(reason)

    return min(score, 100), sorted(reason_set)


def compute_risk_from_record(
    record: dict[str, Any],
    is_agentic: bool = False,
) -> tuple[int, list[str], set[str]]:
    """Insights-time risk from a serialized provenance record dict.

    Returns (raw score, reasons, categories). Score is NOT capped here;
    callers (get_risk_assessment in insights_service) apply min(score, 100).

    Available on Plus/Max only — only reachable via the insights endpoint which
    is already gated by require_non_solo.

    is_agentic: derived from get_agent_context(record) in insights_service.
    """
    prompt_status = str(record.get("promptStatus") or "").strip().lower()
    inserted_code = str(
        _pick_first(record, [["insertion", "extractedInsertedCodeBlock"], ["insertedText"]]) or ""
    )
    file_path = str(_pick_first(record, [["file", "path"], ["filePath"]]) or "")
    net_added_lines = int(
        _pick_first(record, [["insertion", "netAddedLines"], ["netAddedLines"]]) or 0
    )

    correlation_confidence: float | None = None
    raw_conf = _pick_first(record, [["metadata", "correlationConfidence"]])
    if raw_conf is not None:
        try:
            correlation_confidence = float(raw_conf)
        except (TypeError, ValueError):
            pass

    score = 12
    reasons: list[str] = []
    categories: set[str] = set()

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

    for patterns, delta, reason, category in _CODE_PATTERN_RULES:
        if _contains_pattern(inserted_code, patterns):
            score += delta
            reasons.append(reason)
            categories.add(category)

    for patterns, delta, reason, category in _FILE_PATTERN_RULES:
        if _contains_pattern(file_path, patterns):
            score += delta
            reasons.append(reason)
            categories.add(category)

    if is_agentic:
        score += 6
        reasons.append("The record appears to come from an autonomous or semi-autonomous coding session.")
        categories.add("provenance")

    return score, reasons, categories
