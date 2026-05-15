from __future__ import annotations

import re


_BRANCH_PATTERNS = [
    r"\bif\b", r"\belif\b", r"\belse\b", r"\bfor\b", r"\bwhile\b",
    r"\bcase\b", r"\bcatch\b", r"\bexcept\b", r"\bfinally\b",
    r"\band\b", r"\bor\b", r"\&\&", r"\|\|", r"\?\s*[^:]", r"\bswitch\b",
]


def compute_code_metrics(code: str, file_path: str = "") -> dict:
    """Compute code quality metrics for an AI-generated code block (no external deps)."""
    if not code or not code.strip():
        return _empty_metrics()

    lines = code.splitlines()
    total_lines = len(lines)
    blank_lines = sum(1 for ln in lines if not ln.strip())

    lang = _detect_language(file_path)
    comment_lines = _count_comment_lines(lines, lang)
    code_lines = max(0, total_lines - blank_lines - comment_lines)
    comment_ratio = round(comment_lines / max(total_lines, 1), 3)

    function_count = _count_functions(code, lang)
    class_count = _count_classes(code, lang)
    cyclomatic = _estimate_cyclomatic_complexity(code)
    maintainability = _compute_maintainability(code_lines, cyclomatic, comment_ratio)

    return {
        "totalLines": total_lines,
        "codeLines": code_lines,
        "blankLines": blank_lines,
        "commentLines": comment_lines,
        "commentRatio": comment_ratio,
        "functionCount": function_count,
        "classCount": class_count,
        "cyclomaticComplexity": cyclomatic,
        "maintainabilityScore": maintainability,
        "language": lang,
    }


def _detect_language(file_path: str) -> str:
    p = (file_path or "").lower()
    if p.endswith((".py", ".pyi")):
        return "python"
    if p.endswith((".ts", ".tsx", ".mts", ".cts")):
        return "typescript"
    if p.endswith((".js", ".jsx", ".mjs", ".cjs")):
        return "javascript"
    if p.endswith((".java",)):
        return "java"
    if p.endswith((".go",)):
        return "go"
    if p.endswith((".rs",)):
        return "rust"
    if p.endswith((".rb",)):
        return "ruby"
    if p.endswith((".cs",)):
        return "csharp"
    if p.endswith((".cpp", ".cc", ".cxx", ".c", ".h", ".hpp")):
        return "c"
    if p.endswith((".swift",)):
        return "swift"
    if p.endswith((".kt", ".kts")):
        return "kotlin"
    return "unknown"


def _count_comment_lines(lines: list[str], lang: str) -> int:
    count = 0
    in_block = False
    block_end = ""

    block_starters: dict[str, tuple[str, str]] = {
        "python": ('"""', '"""'),
        "default": ("/*", "*/"),
    }
    bs, be = block_starters.get(lang, block_starters["default"])
    bs2: str | None = "'''" if lang == "python" else None
    be2: str | None = "'''" if lang == "python" else None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if in_block:
            count += 1
            if block_end in stripped:
                in_block = False
            continue

        if lang == "python" and stripped.startswith("#"):
            count += 1
            continue
        if lang != "python" and stripped.startswith("//"):
            count += 1
            continue

        for opener, closer in ((bs, be), (bs2, be2)):
            if opener and opener in stripped:
                in_block = True
                block_end = closer or be
                count += 1
                tail = stripped[stripped.index(opener) + len(opener):]
                if closer and closer in tail:
                    in_block = False
                break

    return count


def _count_functions(code: str, lang: str) -> int:
    if lang == "python":
        return len(re.findall(r"^\s*(?:async\s+)?def\s+\w+", code, re.MULTILINE))
    if lang in ("javascript", "typescript"):
        return len(re.findall(
            r"(?:(?:async\s+)?function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\(|^\s*(?:async\s+)?\w+\s*\(.*\)\s*\{)",
            code, re.MULTILINE
        ))
    if lang == "java":
        return len(re.findall(r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+\w+\s*\(", code))
    if lang == "go":
        return len(re.findall(r"^func\s+", code, re.MULTILINE))
    if lang == "rust":
        return len(re.findall(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+\w+", code, re.MULTILINE))
    if lang in ("csharp", "kotlin", "swift"):
        return len(re.findall(r"(?:func|fun|void|async)\s+\w+\s*\(", code))
    return len(re.findall(r"(?:function|func|def|fn|sub)\s+\w+", code))


def _count_classes(code: str, lang: str) -> int:
    if lang == "python":
        return len(re.findall(r"^\s*class\s+\w+", code, re.MULTILINE))
    if lang in ("java", "csharp", "kotlin"):
        return len(re.findall(r"\b(?:class|interface|enum|object)\s+\w+", code))
    if lang in ("javascript", "typescript"):
        return len(re.findall(r"\bclass\s+\w+", code))
    if lang in ("rust", "go"):
        return len(re.findall(r"\b(?:struct|trait|interface)\s+\w+", code))
    return 0


def _estimate_cyclomatic_complexity(code: str) -> int:
    count = 1
    for p in _BRANCH_PATTERNS:
        count += len(re.findall(p, code))
    return min(count, 999)


def _compute_maintainability(code_lines: int, cyclomatic: int, comment_ratio: float) -> int:
    score = 100.0
    score -= min(cyclomatic * 2, 40)
    if code_lines > 300:
        score -= 30
    elif code_lines > 150:
        score -= 15
    elif code_lines > 75:
        score -= 8
    score += min(comment_ratio * 20, 10)
    return max(0, min(100, round(score)))


def _empty_metrics() -> dict:
    return {
        "totalLines": 0, "codeLines": 0, "blankLines": 0, "commentLines": 0,
        "commentRatio": 0.0, "functionCount": 0, "classCount": 0,
        "cyclomaticComplexity": 1, "maintainabilityScore": 100, "language": "unknown",
    }
