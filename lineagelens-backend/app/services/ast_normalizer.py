import re
from pathlib import Path

try:
    from tree_sitter_languages import get_parser
except Exception:  # pragma: no cover - handled by fallback path
    get_parser = None


IGNORED_NODE_TYPES = {
    "identifier",
    "field_identifier",
    "property_identifier",
    "type_identifier",
    "string",
    "string_literal",
    "template_string",
    "number",
    "number_literal",
    "integer",
    "float",
    "true",
    "false",
    "null",
    "none",
    "comment",
}

IGNORED_PATTERNS = [
    re.compile(r"identifier$", re.IGNORECASE),
    re.compile(r"literal$", re.IGNORECASE),
    re.compile(r"^string", re.IGNORECASE),
    re.compile(r"^number", re.IGNORECASE),
    re.compile(r"comment", re.IGNORECASE),
]


def normalize_ast_tokens(code: str, language_hint: str | None = None) -> list[str]:
    source = (code or "").strip()
    if not source:
        return []

    language = detect_language(source, language_hint)

    if get_parser is None:
        return fallback_structural_tokens(source)

    try:
        parser = get_parser(language)
        tree = parser.parse(source.encode("utf-8"))
    except Exception:
        return fallback_structural_tokens(source)

    root = tree.root_node
    if root is None:
        return fallback_structural_tokens(source)

    tokens: list[str] = []
    traverse(root, tokens)
    return tokens


def detect_language(code: str, language_hint: str | None) -> str:
    normalized_hint = (language_hint or "").strip().lower()

    suffix = Path(normalized_hint).suffix.lower()
    if suffix in {".py", ".pyi"}:
        return "python"
    if suffix in {".ts", ".mts", ".cts"}:
        return "typescript"
    if suffix in {".tsx", ".jsx"}:
        return "tsx"
    if suffix in {".js", ".mjs", ".cjs"}:
        return "javascript"

    if normalized_hint in {"python", "py"}:
        return "python"
    if normalized_hint in {"typescript", "ts", "typescriptreact", "tsx"}:
        return "typescript"
    if normalized_hint in {"javascript", "js", "jsx"}:
        return "javascript"

    if re.search(r"(^|\n)\s*def\s+\w+\s*\(", code):
        return "python"
    if re.search(r"(^|\n)\s*from\s+\w[\w.]*\s+import\s+", code):
        return "python"
    if re.search(r"\binterface\s+\w+", code):
        return "typescript"
    if re.search(r"\benum\s+\w+", code):
        return "typescript"

    return "javascript"


def traverse(node, output: list[str]) -> None:
    node_type = getattr(node, "type", "")
    if should_include(node_type):
        output.append(node_type)

    for child in getattr(node, "named_children", []):
        traverse(child, output)


def should_include(node_type: str) -> bool:
    normalized = (node_type or "").strip().lower()
    if not normalized:
        return False

    if normalized in IGNORED_NODE_TYPES:
        return False

    for pattern in IGNORED_PATTERNS:
        if pattern.search(normalized):
            return False

    return True


def fallback_structural_tokens(code: str) -> list[str]:
    lines = code.splitlines()
    tokens: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("def "):
            tokens.append("function_definition")
        elif stripped.startswith("class "):
            tokens.append("class_definition")
        elif stripped.startswith("if "):
            tokens.append("if_statement")
        elif stripped.startswith("for "):
            tokens.append("for_statement")
        elif stripped.startswith("while "):
            tokens.append("while_statement")
        elif stripped.startswith("return"):
            tokens.append("return_statement")
        elif stripped.startswith("import ") or stripped.startswith("from "):
            tokens.append("import_statement")
        else:
            tokens.append("statement")

    return tokens
