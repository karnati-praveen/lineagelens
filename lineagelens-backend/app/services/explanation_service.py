from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib import error as url_error
from urllib import request as url_request

from app.core.config import Settings


logger = logging.getLogger(__name__)

EXPLANATION_SYSTEM_PROMPT = """You are an expert software provenance analyst.
Your task is to explain, in plain English, why a generated code change was likely written this way.
Use only the provided provenance data: original prompt/messages, model/parameters, inserted code,
surrounding context, dependency snapshot, and lineage/evolution history.
Do not invent requirements or hidden decisions.
If details are missing, explicitly say what is unknown.

Output style requirements:
1) Start with a one-sentence summary of intent.
2) Then provide concise bullet points covering:
   - Prompt intent and constraints
   - How surrounding code/context influenced structure
   - Why key implementation choices were likely made
   - Any trade-offs or risks implied by the code
3) Keep the explanation practical and specific to the provided code.
4) Avoid repeating raw JSON unless needed for clarity.
"""


def _pick_nested(data: dict[str, Any], path: list[str]) -> Any:
    cursor: Any = data
    for segment in path:
        if not isinstance(cursor, dict) or segment not in cursor:
            return None
        cursor = cursor[segment]
    return cursor


def _safe_json(value: Any, max_chars: int = 4500) -> str:
    try:
        text = json.dumps(value, indent=2, default=str)
    except Exception:
        text = str(value)

    if len(text) > max_chars:
        return text[:max_chars] + "\n...<truncated>"

    return text


def _build_prompt_context(record: dict[str, Any]) -> dict[str, Any]:
    prompt_messages = (
        _pick_nested(record, ["prompt", "fullMessages"])
        or _pick_nested(record, ["prompt", "messages"])
        or _pick_nested(record, ["provenance", "fullPromptMessages"])
        or _pick_nested(record, ["provenance", "messages"])
        or _pick_nested(record, ["correlation", "messages"])
        or record.get("messages")
    )

    model_name = (
        _pick_nested(record, ["prompt", "modelName"])
        or _pick_nested(record, ["provenance", "modelName"])
        or record.get("model")
        or record.get("modelName")
    )

    parameters = (
        _pick_nested(record, ["prompt", "parameters"])
        or _pick_nested(record, ["provenance", "parameters"])
        or record.get("parameters")
    )

    inserted_code = (
        _pick_nested(record, ["insertion", "extractedInsertedCodeBlock"])
        or record.get("insertedText")
        or record.get("inserted_code")
        or ""
    )

    surrounding_context = (
        _pick_nested(record, ["insertion", "surroundingContext"])
        or record.get("surroundingContext")
        or record.get("surrounding_context")
    )

    context_snapshot = record.get("contextSnapshot") or record.get("context_snapshot")

    lineage = (
        record.get("evolutionChain")
        or record.get("lineage")
        or record.get("versions")
        or _pick_nested(record, ["lineage", "versions"])
    )

    return {
        "promptMessages": prompt_messages,
        "modelName": model_name,
        "parameters": parameters,
        "insertedCode": inserted_code,
        "surroundingContext": surrounding_context,
        "contextSnapshot": context_snapshot,
        "lineage": lineage,
    }


def _build_user_prompt(record: dict[str, Any]) -> str:
    context = _build_prompt_context(record)

    compact_payload = {
        "uuid": record.get("uuid") or record.get("id"),
        "timestampIso": record.get("timestampIso") or record.get("insertionTimestampIso"),
        "filePath": _pick_nested(record, ["file", "path"]) or record.get("filePath"),
        "modelName": context["modelName"],
        "modelParameters": context["parameters"],
        "promptMessages": context["promptMessages"],
        "insertedCode": context["insertedCode"],
        "surroundingContext": context["surroundingContext"],
        "contextSnapshot": context["contextSnapshot"],
        "lineage": context["lineage"],
    }

    return (
        "Explain why this code was likely written this way based on provenance data.\n\n"
        "Provenance payload:\n"
        + _safe_json(compact_payload)
    )


async def generate_plain_english_explanation(
    record: dict[str, Any], settings: Settings
) -> tuple[str, str, str]:
    llm_text = await _try_generate_with_llm(record, settings)
    if llm_text:
        return llm_text, settings.explain_llm_model, "llm"

    return _heuristic_explanation(record), "heuristic-fallback-v1", "fallback"


async def _try_generate_with_llm(record: dict[str, Any], settings: Settings) -> str | None:
    api_key = (settings.explain_llm_api_key or "").strip()
    if not api_key:
        return None

    request_body = {
        "model": settings.explain_llm_model,
        "temperature": 0.2,
        "max_tokens": 500,
        "messages": [
            {
                "role": "system",
                "content": EXPLANATION_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": _build_user_prompt(record),
            },
        ],
    }

    result: str | None = None
    for attempt in range(3):
        try:
            result = await asyncio.to_thread(
                _call_llm_sync,
                settings.explain_llm_api_url,
                api_key,
                request_body,
                settings.explain_llm_timeout_seconds,
            )
            break
        except Exception as exc:
            if attempt == 2:
                logger.warning("LLM explain failed after 3 attempts, using heuristic: %s", exc)
                result = None
                break
            await asyncio.sleep(2 ** attempt)  # 1s, 2s

    return result


def _call_llm_sync(
    endpoint_url: str,
    api_key: str,
    body: dict[str, Any],
    timeout_seconds: int,
) -> str | None:
    payload = json.dumps(body).encode("utf-8")

    req = url_request.Request(
        endpoint_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with url_request.urlopen(req, timeout=timeout_seconds) as response:
            data = response.read().decode("utf-8")
    except (url_error.URLError, TimeoutError):
        return None

    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None

    return _extract_llm_text(parsed)


def _extract_llm_text(response_payload: dict[str, Any]) -> str | None:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None

    message = first_choice.get("message")
    if not isinstance(message, dict):
        return None

    content = message.get("content")

    if isinstance(content, str):
        text = content.strip()
        return text if text else None

    if isinstance(content, list):
        return _join_text_content_list(content)

    return None


def _join_text_content_list(content: list[Any]) -> str | None:
    chunks: list[str] = []
    for chunk in content:
        if isinstance(chunk, dict):
            text_part = chunk.get("text")
            if isinstance(text_part, str) and text_part.strip():
                chunks.append(text_part.strip())
        elif isinstance(chunk, str) and chunk.strip():
            chunks.append(chunk.strip())
    return "\n".join(chunks) if chunks else None


def _heuristic_explanation(record: dict[str, Any]) -> str:
    context = _build_prompt_context(record)

    prompt_messages = context.get("promptMessages")
    model_name = context.get("modelName")
    parameters = context.get("parameters")
    inserted_code = str(context.get("insertedCode") or "")
    surrounding_context = context.get("surroundingContext")
    context_snapshot = context.get("contextSnapshot")
    lineage = context.get("lineage")

    summary_parts: list[str] = []

    if prompt_messages:
        summary_parts.append(
            "The code appears to be driven by a prompt that requested behavior aligned with the inserted implementation."
        )

    if model_name:
        summary_parts.append(f"The generation was produced by model {model_name}.")

    if isinstance(parameters, dict) and parameters:
        if "temperature" in parameters:
            summary_parts.append(
                "A temperature setting was provided, which likely influenced how deterministic versus creative the output is."
            )

    if inserted_code:
        summary_parts.append(
            "The inserted block structure suggests the model prioritized integrating directly into existing flow instead of introducing unrelated abstractions."
        )

    if surrounding_context:
        summary_parts.append(
            "Nearby code context likely constrained naming, control flow, and data handling choices to remain consistent with the file."
        )

    if context_snapshot:
        summary_parts.append(
            "Project configuration and dependency context likely shaped framework-specific implementation details."
        )

    if lineage:
        # Count prior versions and surface the earliest insertion date if available
        lineage_versions: list[Any] = lineage if isinstance(lineage, list) else []
        prior_count = max(0, len(lineage_versions) - 1)
        earliest_date: str | None = None
        if lineage_versions:
            first_entry = lineage_versions[0]
            if isinstance(first_entry, dict):
                earliest_date = str(
                    first_entry.get("createdAt")
                    or first_entry.get("timestamp")
                    or first_entry.get("insertionTimestampIso")
                    or ""
                ) or None

        if prior_count > 0 and earliest_date:
            summary_parts.append(
                f"This code has {prior_count} prior AI-generated version(s) in the lineage chain. "
                f"It evolved from an earlier insertion on {earliest_date[:10]}."
            )
        elif prior_count > 0:
            summary_parts.append(
                f"This code has {prior_count} prior AI-generated version(s) in the lineage chain."
            )
        else:
            summary_parts.append(
                "Lineage history indicates this block may have evolved from earlier generated versions rather than being entirely new."
            )

    if not summary_parts:
        summary_parts.append(
            "The provenance payload does not include enough structured context to produce a precise rationale."
        )

    bullets = [
        "- Prompt intent and constraints: inferred from available prompt metadata.",
        "- Context influence: surrounding file content and project snapshot likely guided compatibility.",
        "- Implementation choices: structure reflects fit with existing patterns and dependencies.",
        "- Trade-offs: automated generation may optimize speed and consistency but can miss domain nuances.",
    ]

    intro = " ".join(summary_parts)

    return (
        f"{intro}\n\n"
        "Key factors:\n"
        + "\n".join(bullets)
        + "\n\n"
        f"Generated at {datetime.now(tz=UTC).isoformat()} by fallback explainer."
    )
