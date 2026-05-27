from __future__ import annotations

# NOTE: risk assessment logic is also partially implemented in
# app.services.insights_service (_compute_heuristic_risk).  Do not merge
# without careful regression testing of both paths.

import re


_CODE_PATTERN_RULES: list[tuple[list[str], int, str]] = [
    ([r"api[_-]?key", r"access[_-]?token", r"private[_-]?key"], 28,
     "The inserted block appears to contain credential-like material."),
    ([r"\beval\b", r"\bFunction\s*\(", r"new Function", r"\bexec\s*\(", r"\bexecSync\s*\("], 24,
     "Dynamic code execution is present in the generated block."),
    ([r"\bsubprocess\.", r"\bos\.system\b", r"\bchild_process\b", r"\bspawn(?:Sync)?\b"], 22,
     "Shell or process execution was introduced by the generated block."),
    ([r"dangerouslySetInnerHTML", r"\binnerHTML\s*="], 20,
     "Unsafe DOM mutation patterns were introduced."),
    ([r"\bSELECT\s+.+\bFROM\b", r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w+\s+SET\b", r"\bDELETE\s+FROM\b"], 16,
     "Raw SQL appears in the generated block."),
    ([r"\bpassword\b", r"\btoken\b", r"\bauth\b", r"\bcredential\b"], 12,
     "Authentication or credential handling appears in the generated block."),
]

_FILE_PATTERN_RULES: list[tuple[list[str], int, str]] = [
    ([r"auth", r"security", r"permission", r"oauth", r"token", r"secret", r"credential"], 14,
     "The file path suggests a security-sensitive surface."),
    ([r"payment", r"billing", r"invoice", r"ledger", r"finance"], 14,
     "The file path suggests a financially sensitive surface."),
]


def _contains_pattern(value: str, patterns: list[str]) -> bool:
    return any(re.search(p, value, flags=re.IGNORECASE) for p in patterns)


def compute_risk_score(
    inserted_code: str,
    prompt_messages: object | None = None,
    model_name: str | None = None,
    file_path: str | None = None,
) -> tuple[int, list[str]]:
    """Return (score 0-100, deduplicated ordered reasons).

    The heuristic mirrors the logic in insights_service._compute_heuristic_risk
    but operates on raw field values rather than a serialized record dict,
    making it suitable for call-time computation during ingest.
    """
    score = 12
    reason_set: set[str] = set()

    # --- Prompt capture signal ---
    # prompt_messages being None means not captured
    if prompt_messages is None:
        score += 24
        reason_set.add("Prompt capture is missing, which reduces auditability and reviewer confidence.")

    # --- Size signal (estimate from inserted_code) ---
    code_str = inserted_code or ""
    net_lines = max(1, code_str.count("\n") + 1) if code_str.strip() else 0

    if net_lines >= 80:
        score += 18
        reason_set.add("A large AI-generated block was introduced.")
    elif net_lines >= 30:
        score += 10
        reason_set.add("The generated block is large enough to warrant focused review.")

    # --- Code pattern rules ---
    for patterns, delta, reason in _CODE_PATTERN_RULES:
        if _contains_pattern(code_str, patterns):
            score += delta
            reason_set.add(reason)

    # --- File path pattern rules ---
    fp = file_path or ""
    for patterns, delta, reason in _FILE_PATTERN_RULES:
        if _contains_pattern(fp, patterns):
            score += delta
            reason_set.add(reason)

    final_score = min(score, 100)
    return final_score, sorted(reason_set)
