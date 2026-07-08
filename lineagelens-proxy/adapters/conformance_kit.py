"""Cross-adapter conformance kit (PART 5 #54).

Runs the same shared, synthetic (non-real) golden fixture set through each of
the four adapters' `_parse_*_to_edits` functions and asserts:
  - the module declares a CAPABILITY matching its own provider name,
  - every non-empty edit-dict output conforms to the CanonicalEdit contract
    (required fields present and correctly typed — extra adapter-specific
    fields like codex/gemini's verb/moved_to are allowed),
  - the fixture's expected edit count for each case is met.

This is the "one shared conformance test kit run against all four adapters"
called out as missing in the doc.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

from adapters.contract import validate_edit_dicts

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# provider -> (module import path, parse-function name, fixture file name)
ADAPTER_SPECS: dict[str, tuple[str, str, str]] = {
    "anthropic": ("adapters.anthropic", "_parse_anthropic_tool_use_to_edits", "anthropic_edit.json"),
    "codex": ("adapters.codex", "_parse_codex_function_call_to_edits", "codex_edit.json"),
    "gemini": ("adapters.gemini", "_parse_gemini_function_call_to_edits", "gemini_edit.json"),
    "openai_chat": ("adapters.openai_chat", "_parse_openai_tool_call_to_edits", "openai_chat_edit.json"),
}


def run_conformance_suite(provider: str) -> list[str]:
    """Run *provider*'s shared fixture through its parse function.

    Returns a list of contract violations; empty list means fully conformant.
    """
    if provider not in ADAPTER_SPECS:
        return [f"unknown provider {provider!r}"]

    module_name, fn_name, fixture_name = ADAPTER_SPECS[provider]
    module = importlib.import_module(module_name)

    problems: list[str] = []

    capability = getattr(module, "CAPABILITY", None)
    if capability is None:
        problems.append(f"{module_name} does not declare CAPABILITY")
    elif capability.provider != provider:
        problems.append(f"{module_name}.CAPABILITY.provider={capability.provider!r} != {provider!r}")

    parse_fn = getattr(module, fn_name, None)
    if parse_fn is None:
        problems.append(f"{module_name} has no {fn_name}")
        return problems

    fixture = json.loads((_FIXTURES_DIR / fixture_name).read_text(encoding="utf-8"))

    for case in fixture["cases"]:
        name = case["name"]
        try:
            edits = parse_fn(case["input"])
        except Exception as exc:
            problems.append(f"{provider}/{name}: parse function raised {exc!r} instead of degrading to []")
            continue

        expected_count = case.get("expectedEditCount")
        if expected_count is not None and len(edits) != expected_count:
            problems.append(f"{provider}/{name}: expected {expected_count} edit(s), got {len(edits)}")

        problems.extend(f"{provider}/{name}: {p}" for p in validate_edit_dicts(edits))

    return problems


def run_all_conformance_suites() -> dict[str, list[str]]:
    """Run every adapter's conformance suite. Used by the shared test kit."""
    return {provider: run_conformance_suite(provider) for provider in ADAPTER_SPECS}
