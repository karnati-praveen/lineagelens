"""
Static pricing table for LLM models (USD per 1 million tokens).

Used to estimate cost savings when a request is routed from an expensive
model to a cheaper one.  Prices are approximate list prices and may lag
behind provider changes — they're used only for display/analytics, not
for billing.

Update this table whenever providers announce pricing changes.
"""
from __future__ import annotations

# Keys are canonical model IDs as they appear in provider request bodies.
# Values: input_per_1m and output_per_1m are USD per 1 000 000 tokens.
_PRICING: dict[str, dict[str, float]] = {
    # ── Anthropic ────────────────────────────────────────────────────────────
    "claude-opus-4-7":              {"input_per_1m": 15.00, "output_per_1m": 75.00},
    "claude-opus-4-6":              {"input_per_1m": 15.00, "output_per_1m": 75.00},
    "claude-opus-4-5":              {"input_per_1m": 15.00, "output_per_1m": 75.00},
    "claude-sonnet-4-6":            {"input_per_1m":  3.00, "output_per_1m": 15.00},
    "claude-sonnet-4-5":            {"input_per_1m":  3.00, "output_per_1m": 15.00},
    "claude-haiku-4-5-20251001":    {"input_per_1m":  0.25, "output_per_1m":  1.25},
    "claude-haiku-4-5":             {"input_per_1m":  0.25, "output_per_1m":  1.25},
    "claude-3-5-sonnet-20241022":   {"input_per_1m":  3.00, "output_per_1m": 15.00},
    "claude-3-5-haiku-20241022":    {"input_per_1m":  0.80, "output_per_1m":  4.00},
    "claude-3-opus-20240229":       {"input_per_1m": 15.00, "output_per_1m": 75.00},
    # ── OpenAI ───────────────────────────────────────────────────────────────
    "gpt-4o":                       {"input_per_1m":  5.00, "output_per_1m": 15.00},
    "gpt-4o-2024-11-20":            {"input_per_1m":  2.50, "output_per_1m": 10.00},
    "gpt-4o-mini":                  {"input_per_1m":  0.15, "output_per_1m":  0.60},
    "gpt-4o-mini-2024-07-18":       {"input_per_1m":  0.15, "output_per_1m":  0.60},
    "gpt-4-turbo":                  {"input_per_1m": 10.00, "output_per_1m": 30.00},
    "o3":                           {"input_per_1m":  2.00, "output_per_1m":  8.00},
    "o4-mini":                      {"input_per_1m":  1.10, "output_per_1m":  4.40},
    # ── Gemini ───────────────────────────────────────────────────────────────
    "gemini-2.5-pro":               {"input_per_1m":  1.25, "output_per_1m": 10.00},
    "gemini-2.5-pro-preview":       {"input_per_1m":  1.25, "output_per_1m": 10.00},
    "gemini-2.5-flash":             {"input_per_1m":  0.075, "output_per_1m": 0.30},
    "gemini-2.5-flash-preview":     {"input_per_1m":  0.075, "output_per_1m": 0.30},
    "gemini-2.0-flash":             {"input_per_1m":  0.10,  "output_per_1m": 0.40},
    "gemini-1.5-pro":               {"input_per_1m":  1.25,  "output_per_1m": 5.00},
    "gemini-1.5-flash":             {"input_per_1m":  0.075, "output_per_1m": 0.30},
}


def get_pricing(model_id: str) -> dict[str, float] | None:
    """Return {input_per_1m, output_per_1m} for *model_id*, or None if unknown.

    Matching is prefix-based so that model IDs with date suffixes
    (e.g. "claude-haiku-4-5-20251001-preview") fall back gracefully.
    """
    if model_id in _PRICING:
        return _PRICING[model_id]
    # Prefix fallback: find the longest key that is a prefix of model_id
    best: tuple[int, dict[str, float]] | None = None
    for key, val in _PRICING.items():
        if model_id.startswith(key):
            if best is None or len(key) > best[0]:
                best = (len(key), val)
    return best[1] if best else None


def estimate_savings(
    original_model: str,
    routed_model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate cost savings (USD) from routing *original* → *routed*.

    Returns 0.0 when pricing data is unavailable or savings would be negative
    (i.e. routing to a more expensive model for this tier).
    """
    orig = get_pricing(original_model)
    routed = get_pricing(routed_model)
    if not orig or not routed:
        return 0.0
    savings = (
        input_tokens  * (orig["input_per_1m"]  - routed["input_per_1m"]) +
        output_tokens * (orig["output_per_1m"] - routed["output_per_1m"])
    ) / 1_000_000
    return max(0.0, savings)
