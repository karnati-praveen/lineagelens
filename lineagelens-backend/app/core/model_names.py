from __future__ import annotations

_CANONICAL: dict[str, str] = {
    # OpenAI
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o Mini",
    "gpt-4-turbo": "GPT-4 Turbo",
    "gpt-4": "GPT-4",
    "gpt-3.5-turbo": "GPT-3.5 Turbo",
    "o1": "o1",
    "o1-mini": "o1 Mini",
    "o3": "o3",
    "o3-mini": "o3 Mini",
    # Anthropic
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
    "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet",
    "claude-3-5-haiku-20241022": "Claude 3.5 Haiku",
    "claude-3-opus-20240229": "Claude 3 Opus",
    # Google
    "gemini-2.0-flash": "Gemini 2.0 Flash",
    "gemini-1.5-pro": "Gemini 1.5 Pro",
    "gemini-1.5-flash": "Gemini 1.5 Flash",
    # Meta
    "llama-3.1-405b-instruct": "Llama 3.1 405B",
    "llama-3.1-70b-instruct": "Llama 3.1 70B",
    "llama-3.1-8b-instruct": "Llama 3.1 8B",
}

_MISTRAL_AI = "Mistral AI"

_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("claude", "Anthropic"),
    ("gpt", "OpenAI"),
    ("o1", "OpenAI"),
    ("o3", "OpenAI"),
    ("gemini", "Google"),
    ("llama", "Meta"),
    ("mistral", _MISTRAL_AI),
    ("mixtral", _MISTRAL_AI),
    ("codestral", _MISTRAL_AI),
    ("deepseek", "DeepSeek"),
    ("qwen", "Alibaba"),
]


def normalize_model_name(raw: str | None) -> str | None:
    if not raw:
        return raw
    key = raw.strip().lower()
    if key in _CANONICAL:
        return _CANONICAL[key]
    # Title-case the raw value as fallback
    return raw.strip().title()


def infer_provider(model_name: str | None) -> str | None:
    if not model_name:
        return None
    lower = model_name.strip().lower()
    for prefix, provider in _PROVIDER_PREFIXES:
        if lower.startswith(prefix):
            return provider
    return None
