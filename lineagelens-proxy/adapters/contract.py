"""Provider-collapse adapter contract (PART 5 #54).

Each adapter (anthropic, codex, gemini, openai_chat) implements the same
informal shape today — a session-key function, an extract-from-body/SSE
function, a parse-to-edits function, a classify-result function, and
pending-edit store/resolve functions — but that shape has never been written
down anywhere, has no capability declaration, no schema version, and no way
to distinguish "this tool has no side-effect" from "something was here but
we couldn't parse it" (both currently return `[]` from the parse function).

This module is the contract, not a rewrite of the adapters: `CanonicalEdit`
documents the minimum required fields every adapter's edit dicts already
carry (adapters may add extra fields — e.g. codex/gemini's `verb`/
`moved_to` — those are allowed extensions, not violations).
`AdapterCapability` is a static, per-adapter declaration of what it actually
supports, verified against each adapter's real code rather than copy-pasted.
`classify_capture_result()` is an additive, non-breaking helper that
distinguishes RESULT_UNKNOWN (nothing to capture — e.g. a read-only tool)
from RESULT_CAPTURE_UNAVAILABLE (a mutating tool fired but its input could
not be parsed) — existing adapter call sites are unchanged; this is a new,
separately-callable diagnostic layer.

Follow-ups (documented, not built here):
  - OpenTelemetry GenAI semantic-conventions ingestion. Zero otel dependency
    exists in this repo today; a project can implement this same
    CanonicalEdit/AdapterCapability contract once it's actually needed.
  - hook/CI/git/file-diff fallback capture (a new capture surface, not a
    contract fix to the four existing provider adapters).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

ADAPTER_CONTRACT_VERSION = "1.0"

# Required keys every adapter's edit dict must carry. Adapters may add more
# (codex/gemini add `verb`/`moved_to`) — those are allowed extensions.
CANONICAL_EDIT_REQUIRED_FIELDS = frozenset(
    {"tool_use_id", "tool_name", "edit_index", "file_path", "old_string", "new_string"}
)

# Nothing to capture (e.g. a read-only tool, or a recognized no-op sub-command
# like str_replace_based_edit_tool's "view"). This is an expected, healthy
# outcome — not a failure.
RESULT_UNKNOWN = "unknown"

# A mutating tool fired but its input could not be parsed into an edit (e.g.
# malformed/non-dict arguments). Distinct from RESULT_UNKNOWN so an operator
# can tell "nothing happened here" apart from "something happened here that
# we failed to capture" — today both silently collapse to an empty list.
RESULT_CAPTURE_UNAVAILABLE = "capture_unavailable"

RESULT_CAPTURED = "captured"


class AdapterContractError(Exception):
    """Raised by the conformance kit when an adapter violates the contract.

    Adapters themselves must never raise this at runtime — a parse failure
    must always degrade to an empty edit list, never propagate into the
    request path. This exception exists for test-time verification only.
    """


@dataclass(frozen=True)
class CanonicalEdit:
    """Typed mirror of the edit dict every adapter's parse function returns.

    `to_dict()`/`from_dict()` round-trip through the same plain-dict wire
    format the proxy already uses — this is a documentation/verification
    layer over the existing shape, not a breaking wire-format change.
    """

    tool_use_id: str
    tool_name: str
    edit_index: int
    file_path: str
    old_string: str
    new_string: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CanonicalEdit":
        missing = CANONICAL_EDIT_REQUIRED_FIELDS - d.keys()
        if missing:
            raise AdapterContractError(f"edit dict missing required fields: {sorted(missing)}")
        return cls(
            tool_use_id=d["tool_use_id"],
            tool_name=d["tool_name"],
            edit_index=d["edit_index"],
            file_path=d["file_path"],
            old_string=d["old_string"],
            new_string=d["new_string"],
        )


@dataclass(frozen=True)
class AdapterCapability:
    """Static, per-adapter capability/fidelity declaration.

    fidelity: "full" (tool-call/function-call structured capture),
              "partial" (best-effort text/heuristic capture only),
              "metadata_only" (session/action visible but no edit content).
    """

    provider: str
    contract_version: str = ADAPTER_CONTRACT_VERSION
    supports_multi_edit: bool = False
    supports_streaming: bool = False
    supports_tool_results: bool = False
    fidelity: str = "full"

    def to_dict(self) -> dict:
        return asdict(self)


def validate_edit_dicts(edits: list[dict]) -> list[str]:
    """Return a list of contract violations for a batch of edit dicts (empty = OK)."""
    problems: list[str] = []
    for i, edit in enumerate(edits):
        missing = CANONICAL_EDIT_REQUIRED_FIELDS - edit.keys()
        if missing:
            problems.append(f"edit[{i}] missing required fields: {sorted(missing)}")
            continue
        for key in ("file_path", "old_string", "new_string", "tool_use_id", "tool_name"):
            if not isinstance(edit[key], str):
                problems.append(f"edit[{i}].{key} must be a string, got {type(edit[key]).__name__}")
        if not isinstance(edit["edit_index"], int):
            problems.append(f"edit[{i}].edit_index must be an int")
    return problems


def classify_capture_result(
    *,
    tool_name: str,
    mutating_tool_names: set[str],
    edits: list[dict],
    input_was_dict: bool,
) -> str:
    """Classify what happened when parsing one tool call/use, for diagnostics.

    Purely additive — no existing adapter call site depends on this. Callers
    who want the new honest states can call it alongside the existing parse
    function without changing that function's return contract.
    """
    if tool_name not in mutating_tool_names:
        return RESULT_UNKNOWN
    if edits:
        return RESULT_CAPTURED
    if not input_was_dict:
        return RESULT_CAPTURE_UNAVAILABLE
    return RESULT_UNKNOWN
