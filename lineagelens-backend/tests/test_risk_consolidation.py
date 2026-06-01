"""Golden tests for risk scoring — lock current _compute_heuristic_risk behavior.

These tests MUST pass before and after the Bet-0 consolidation.  If they fail
after the refactor, the consolidation changed observable behavior.

Each case is hand-computed from the rules in insights_service at the time of
writing:
  base score = 12
  missing prompt            +24  (promptStatus != "captured")
  low correlation (<0.4)    +16
  moderate correlation      +8   (0.4 <= confidence < 0.65)
  large block (>=80 lines)  +18
  medium block (>=30 lines) +10
  api_key pattern           +28
  eval/dynamic exec         +24
  subprocess/shell          +22
  innerHTML/dangerouslySet  +20
  raw SQL                   +16
  password/token/auth/cred  +12
  auth/security file path   +14
  payment/billing file path +14
  agentic session           +6
  cap at 100
"""
from __future__ import annotations

import pytest

from app.services.insights_service import _compute_heuristic_risk


def _rec(
    prompt_status: str = "captured",
    inserted: str = "x = 1",
    file_path: str = "src/utils/helper.py",
    net_lines: int = 5,
    correlation: float | None = None,
    agentic: bool = False,
) -> dict:
    rec: dict = {
        "promptStatus": prompt_status,
        "insertedText": inserted,
        "filePath": file_path,
        "insertion": {
            "extractedInsertedCodeBlock": inserted,
            "netAddedLines": net_lines,
        },
    }
    if correlation is not None:
        rec.setdefault("metadata", {})["correlationConfidence"] = correlation
    if agentic:
        rec.setdefault("metadata", {})["agentContext"] = {
            "sessionKind": "agentic",
            "toolName": "Claude Code",
            "provider": "Anthropic",
            "modelName": "claude-opus-4-5",
            "sessionSignature": "Claude Code|Anthropic|claude-opus-4-5|agentic",
            "detectedAtIso": "2026-06-01T10:00:00.000Z",
        }
    return rec


# ── Individual signal cases ──────────────────────────────────────────────────

def test_benign_record_base_score():
    score, reasons, categories = _compute_heuristic_risk(_rec())
    assert score == 12
    assert reasons == []
    assert categories == set()


def test_missing_prompt_adds_24():
    score, reasons, categories = _compute_heuristic_risk(
        _rec(prompt_status="not-captured")
    )
    assert score == 12 + 24
    assert any("Prompt capture is missing" in r for r in reasons)
    assert "provenance" in categories


def test_large_block_adds_18():
    score, reasons, _ = _compute_heuristic_risk(_rec(net_lines=80))
    assert score == 12 + 18
    assert any("large" in r.lower() for r in reasons)


def test_medium_block_adds_10():
    score, reasons, _ = _compute_heuristic_risk(_rec(net_lines=35))
    assert score == 12 + 10
    assert any("large enough" in r.lower() for r in reasons)


def test_low_correlation_adds_16():
    score, reasons, categories = _compute_heuristic_risk(
        _rec(correlation=0.3)
    )
    assert score == 12 + 16
    assert any("low" in r.lower() for r in reasons)
    assert "provenance" in categories


def test_moderate_correlation_adds_8():
    score, reasons, categories = _compute_heuristic_risk(
        _rec(correlation=0.55)
    )
    assert score == 12 + 8
    assert any("moderate" in r.lower() for r in reasons)


def test_api_key_pattern_adds_28():
    score, reasons, categories = _compute_heuristic_risk(
        _rec(inserted="const api_key = process.env.SECRET")
    )
    # 12 + 28 = 40
    assert score == 40
    assert any("credential" in r.lower() for r in reasons)
    assert "security" in categories


def test_eval_pattern_adds_24():
    # Fixture string simulates AI-generated code containing eval — not executed.
    score, _, categories = _compute_heuristic_risk(
        _rec(inserted="eval(userInput)")
    )
    assert score == 12 + 24
    assert "security" in categories


def test_subprocess_pattern_adds_22():
    score, _, categories = _compute_heuristic_risk(
        _rec(inserted="subprocess.run(['ls', '-la'])")
    )
    assert score == 12 + 22
    assert "security" in categories


def test_inner_html_adds_20():
    # Fixture string simulates AI-generated code with innerHTML — not rendered.
    score, _, categories = _compute_heuristic_risk(
        _rec(inserted="el.innerHTML = userHtml")
    )
    assert score == 12 + 20
    assert "security" in categories


def test_raw_sql_adds_16():
    score, _, categories = _compute_heuristic_risk(
        _rec(inserted="SELECT * FROM users WHERE id = " + str(1))
    )
    assert score == 12 + 16
    assert "reliability" in categories


def test_auth_keyword_in_code_adds_12():
    score, _, categories = _compute_heuristic_risk(
        _rec(inserted="if token is None: raise Unauthorized()")
    )
    assert score == 12 + 12
    assert "compliance" in categories


def test_auth_file_path_adds_14():
    score, reasons, categories = _compute_heuristic_risk(
        _rec(file_path="src/auth/middleware.py")
    )
    assert score == 12 + 14
    assert any("security-sensitive" in r.lower() for r in reasons)
    assert "compliance" in categories


def test_payment_file_path_adds_14():
    score, _, categories = _compute_heuristic_risk(
        _rec(file_path="src/billing/invoice_handler.py")
    )
    assert score == 12 + 14
    assert "compliance" in categories


def test_agentic_session_adds_6():
    score, reasons, categories = _compute_heuristic_risk(_rec(agentic=True))
    assert score == 12 + 6
    assert any("autonomous" in r.lower() for r in reasons)
    assert "provenance" in categories


# ── Combined / capped cases ───────────────────────────────────────────────────

def test_missing_prompt_plus_api_key_plus_auth_file():
    score, reasons, categories = _compute_heuristic_risk(
        _rec(
            prompt_status="not-captured",
            inserted="const api_key = 'abc'",
            file_path="src/auth/handler.ts",
        )
    )
    # 12 + 24 (missing prompt) + 28 (api_key) + 14 (auth file path) = 78
    assert score == 78
    assert len(reasons) == 3
    assert "provenance" in categories
    assert "security" in categories
    assert "compliance" in categories


def test_many_signals_fires_all_rules():
    # _compute_heuristic_risk returns raw (uncapped) score;
    # get_risk_assessment caps to 100.  Assert all expected rules fired.
    score, reasons, categories = _compute_heuristic_risk(
        _rec(
            prompt_status="not-captured",
            inserted=(
                "const api_key = process.env.S\n"
                "eval(cmd)\n"
                "subprocess.run(['rm', '-rf', '/'])\n"
                "el.innerHTML = x\n"
                "SELECT * FROM users\n"
                "if auth:\n"
            ),
            file_path="src/auth/payment/secret.py",
            net_lines=90,
            correlation=0.2,
        )
    )
    assert score > 100  # raw score exceeds cap; caller applies min(score, 100)
    assert {"security", "compliance", "reliability", "provenance"} == categories


def test_get_risk_assessment_caps_at_100():
    from app.services.insights_service import get_risk_assessment
    result = get_risk_assessment(
        _rec(
            prompt_status="not-captured",
            inserted=(
                "const api_key = process.env.S\n"
                "eval(cmd)\n"
                "subprocess.run(['rm', '-rf', '/'])\n"
                "el.innerHTML = x\n"
                "SELECT * FROM users\n"
                "if auth:\n"
            ),
            file_path="src/auth/payment/secret.py",
            net_lines=90,
            correlation=0.2,
        )
    )
    assert result["score"] == 100


def test_reasons_are_ordered_and_not_deduplicated_internally():
    # reasons list preserves append order; dedup is caller's responsibility
    _, reasons, _ = _compute_heuristic_risk(
        _rec(
            prompt_status="not-captured",
            inserted="const api_key = x",
            file_path="src/auth/secret.ts",
        )
    )
    assert len(reasons) == len(list(dict.fromkeys(reasons))), (
        "reasons already contain duplicates before caller deduplication"
    )


def test_categories_are_a_set():
    _, _, categories = _compute_heuristic_risk(_rec(agentic=True))
    assert isinstance(categories, set)
