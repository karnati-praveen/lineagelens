"""
Request complexity classifier for dynamic model routing.

Classifies an LLM request body as "simple", "standard", or "complex"
using deterministic rules (no ML / no LLM).

Supports Anthropic (messages[]), OpenAI (messages[]), and Gemini (contents[])
request shapes.

Rules, evaluated in priority order:
  1. tools / functions array non-empty → complex
  2. total prompt char count / 4 > 8000 tokens  → complex
  3. system message contains refactor|design|architect|security|vulnerability|audit → complex
  4. any code fence in prompt with > 100 lines   → standard
  5. last user message < 200 chars and no code fence → simple
  6. default → standard
"""
from __future__ import annotations

import re

_COMPLEX_KEYWORDS = frozenset(
    {"refactor", "design", "architect", "security", "vulnerability", "audit"}
)

_CODE_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


# ── text extraction helpers ──────────────────────────────────────────────────

def _get_all_text(body: dict) -> str:
    """Concatenate all text content in the request body (all roles, all parts)."""
    parts: list[str] = []

    # Gemini: contents[].parts[].text
    for content in body.get("contents", []) or []:
        if not isinstance(content, dict):
            continue
        for part in content.get("parts", []) or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])

    # Anthropic / OpenAI: messages[].content
    for msg in body.get("messages", []) or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])

    # Anthropic / OpenAI: top-level system string or list-of-blocks
    system = body.get("system") or body.get("systemPrompt", "")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])

    # Gemini: systemInstruction
    sys_instr = body.get("systemInstruction") or {}
    if isinstance(sys_instr, dict):
        for part in sys_instr.get("parts", []) or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    elif isinstance(sys_instr, str):
        parts.append(sys_instr)

    return "\n".join(parts)


def _get_system_text(body: dict) -> str:
    """Extract only the system / instruction text."""
    parts: list[str] = []

    system = body.get("system") or body.get("systemPrompt", "")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])

    # Gemini: systemInstruction
    sys_instr = body.get("systemInstruction") or {}
    if isinstance(sys_instr, dict):
        for part in sys_instr.get("parts", []) or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    elif isinstance(sys_instr, str):
        parts.append(sys_instr)

    # Anthropic / OpenAI: messages with role "system" or "developer"
    for msg in body.get("messages", []) or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") in ("system", "developer"):
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        parts.append(block["text"])

    return "\n".join(parts)


def _get_last_user_message(body: dict) -> str | None:
    """Return the text of the final user turn, or None if unavailable."""
    # Anthropic / OpenAI: messages[]
    messages = body.get("messages", []) or []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") in ("user", "human"):
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [
                    b["text"]
                    for b in content
                    if isinstance(b, dict) and isinstance(b.get("text"), str)
                ]
                return "\n".join(texts) if texts else None

    # Gemini: contents[] last user/model alternation
    contents = body.get("contents", []) or []
    for content in reversed(contents):
        if not isinstance(content, dict):
            continue
        if content.get("role") in ("user",):
            texts = [
                p["text"]
                for p in content.get("parts", []) or []
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            ]
            return "\n".join(texts) if texts else None

    return None


# ── main classifier ──────────────────────────────────────────────────────────

def classify_request(body: object) -> str:
    """Return "simple" | "standard" | "complex" for an LLM request body dict.

    If body is not a dict, returns "standard" (safe default — no downgrade).
    """
    if not isinstance(body, dict):
        return "standard"

    # Rule 1: tool use / function calling present → complex
    # Note: empty list (tools=[]) intentionally does NOT trigger this rule —
    # only a non-empty array means the request actually uses tools.
    if body.get("tools") or body.get("functions"):
        return "complex"

    all_text = _get_all_text(body)

    # Rule 2: approximate token count > 8 000 → complex
    if len(all_text) / 4 > 8000:
        return "complex"

    # Rule 3: complex keyword in system prompt → complex
    system_text = _get_system_text(body).lower()
    if any(kw in system_text for kw in _COMPLEX_KEYWORDS):
        return "complex"

    # Rule 4: code fence with > 100 lines → standard
    for match in _CODE_FENCE_RE.finditer(all_text):
        if match.group(1).count("\n") > 100:
            return "standard"

    # Rule 5: short, plain last user message → simple
    last_user = _get_last_user_message(body)
    if last_user is not None and len(last_user) < 200 and "```" not in last_user:
        return "simple"

    # Rule 6: default
    return "standard"
