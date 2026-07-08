"""Cross-adapter conformance kit tests (PART 5 #54).

Runs the shared, synthetic golden fixture set against all four adapters and
asserts each is contract-conformant (CanonicalEdit shape + declared
CAPABILITY) — the "one shared conformance test kit run against all four
adapters" called out as missing in the doc.
"""
import sys

sys.path.insert(0, ".")

import pytest

from adapters.conformance_kit import ADAPTER_SPECS, run_all_conformance_suites, run_conformance_suite


@pytest.mark.parametrize("provider", sorted(ADAPTER_SPECS))
def test_adapter_is_conformant(provider):
    problems = run_conformance_suite(provider)
    assert problems == [], f"{provider} conformance violations: {problems}"


def test_run_all_conformance_suites_covers_all_four_adapters():
    results = run_all_conformance_suites()
    assert set(results) == {"anthropic", "codex", "gemini", "openai_chat"}
    for provider, problems in results.items():
        assert problems == [], f"{provider}: {problems}"


def test_unknown_provider_reports_a_problem_not_a_crash():
    problems = run_conformance_suite("not-a-real-provider")
    assert problems and "unknown provider" in problems[0]
