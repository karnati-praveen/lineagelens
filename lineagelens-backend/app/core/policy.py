from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PolicyViolation:
    policy_id: str
    policy_name: str
    policy_type: str
    action: str
    reason: str


@dataclass
class PolicyEvalResult:
    passed: bool  # False if any blocking policy triggered
    violations: list[PolicyViolation] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)


def evaluate_policies(policies: list, record_data: dict) -> PolicyEvalResult:
    """
    Evaluate a list of Policy model instances against a candidate record dict.
    record_data keys: model_name, inserted_code, risk_score, file_path, prompt_messages, capture_status

    Policy types and their config shapes:
    - "allowlist": {"field": "model_name", "values": ["gpt-4o", "claude-sonnet-4-6"]}
      → block if field value NOT in values
    - "blocklist": {"field": "model_name", "values": ["gpt-3.5-turbo"]}
      → block/flag if field value IS in values
    - "risk_rule": {"threshold": 80, "operator": "gte"}
      → trigger if risk_score >= threshold (operators: gte, gt, lte, lt, eq)
    - "prompt_pattern": {"pattern": "\\bpassword\\b", "flags": "i"}
      → trigger if any prompt message matches regex pattern

    Actions:
    - "block" → passed=False (ingest should be rejected or soft-blocked)
    - "flag" → add to flags list
    - "alert" → add to alerts list
    - "log" → just log it
    - "allow" → explicit allow (no violation recorded)
    """
    result = PolicyEvalResult(passed=True)

    for policy in policies:
        if not policy.enabled:
            continue

        try:
            triggered, reason = _check_policy(policy, record_data)
        except Exception:
            logger.exception("Policy evaluation error for policy %s", policy.id)
            continue

        if not triggered:
            continue

        violation = PolicyViolation(
            policy_id=str(policy.id),
            policy_name=policy.name,
            policy_type=policy.policy_type,
            action=policy.action,
            reason=reason,
        )
        result.violations.append(violation)

        if policy.action == "block":
            result.passed = False
        elif policy.action == "flag":
            result.flags.append(policy.name)
        elif policy.action == "alert":
            result.alerts.append(policy.name)
        elif policy.action == "log":
            logger.info("Policy triggered (log): %s — %s", policy.name, reason)

    return result


def _check_policy(policy, record_data: dict) -> tuple[bool, str]:
    """Returns (triggered, reason)."""
    cfg = policy.config or {}
    ptype = policy.policy_type

    if ptype == "allowlist":
        field_name = cfg.get("field", "model_name")
        allowed_values = [str(v).lower() for v in cfg.get("values", [])]
        value = str(record_data.get(field_name) or "").lower()
        if allowed_values and value not in allowed_values:
            return True, f"Field '{field_name}' value '{value}' not in allowlist"
        return False, ""

    if ptype == "blocklist":
        field_name = cfg.get("field", "model_name")
        blocked_values = [str(v).lower() for v in cfg.get("values", [])]
        value = str(record_data.get(field_name) or "").lower()
        if value in blocked_values:
            return True, f"Field '{field_name}' value '{value}' is in blocklist"
        return False, ""

    if ptype == "risk_rule":
        threshold = int(cfg.get("threshold", 80))
        operator = cfg.get("operator", "gte")
        risk = record_data.get("risk_score") or 0
        ops = {
            "gte": risk >= threshold,
            "gt": risk > threshold,
            "lte": risk <= threshold,
            "lt": risk < threshold,
            "eq": risk == threshold,
        }
        if ops.get(operator, False):
            return True, f"risk_score {risk} {operator} {threshold}"
        return False, ""

    if ptype == "prompt_pattern":
        pattern = cfg.get("pattern", "")
        flags_str = cfg.get("flags", "")
        re_flags = re.IGNORECASE if "i" in flags_str else 0
        if not pattern:
            return False, ""
        prompt_messages = record_data.get("prompt_messages") or []
        text = " ".join(
            str(m.get("content", "")) if isinstance(m, dict) else str(m)
            for m in (prompt_messages if isinstance(prompt_messages, list) else [])
        )
        if re.search(pattern, text, re_flags):
            return True, f"Prompt matched pattern: {pattern[:60]}"
        return False, ""

    return False, ""
