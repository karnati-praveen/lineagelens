#!/usr/bin/env python3

"""PR provenance review bot.

This script analyzes pull request diffs, finds touched AI-generated blocks,
fetches provenance + lineage details from the backend API, and posts a
summarized review comment to the PR.
"""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib import error as url_error
from urllib import request as url_request

UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)

TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

COMMENT_MARKER = "<!-- provenance-review-bot -->"
MAX_PROMPT_PREVIEW_CHARS = 900
MAX_LINEAGE_ENTRIES = 10
DEFAULT_SEARCH_LIMIT = 60


@dataclass(slots=True)
class EnvConfig:
    repository: str
    pr_number: int
    event_name: str
    github_token: str
    backend_base_url: str
    backend_jwt: str
    workspace_id: str | None
    search_limit: int


@dataclass(slots=True)
class TouchedRecord:
    uuid: str
    record: dict[str, Any]
    file_path: str
    evidence: str
    overlap_score: float | None


class HttpError(RuntimeError):
    pass


def main() -> int:
    config = load_env_config()

    github_api = GitHubApi(config.repository, config.github_token)
    backend_api = BackendApi(config.backend_base_url, config.backend_jwt, config.workspace_id)

    files = github_api.list_pull_request_files(config.pr_number)

    touched_by_file: dict[str, list[TouchedRecord]] = {}
    unique_seen: set[str] = set()

    for file_entry in files:
        file_path = str(file_entry.get("filename") or "").strip()
        if not file_path:
            continue

        patch = str(file_entry.get("patch") or "")
        direct_uuids = find_uuids_in_patch(patch)

        touched_for_file: list[TouchedRecord] = []

        for block_uuid in sorted(direct_uuids):
            if block_uuid in unique_seen:
                continue

            record = backend_api.get_provenance(block_uuid)
            if not record:
                continue

            touched_for_file.append(
                TouchedRecord(
                    uuid=block_uuid,
                    record=record,
                    file_path=file_path,
                    evidence="uuid-in-diff",
                    overlap_score=1.0,
                )
            )
            unique_seen.add(block_uuid)

        candidate_results = backend_api.search_by_file(file_path, limit=config.search_limit)
        for candidate in candidate_results:
            candidate_uuid = extract_uuid(candidate)
            if not candidate_uuid or candidate_uuid in unique_seen:
                continue

            record = extract_record(candidate)
            if not record:
                fetched = backend_api.get_provenance(candidate_uuid)
                record = fetched if fetched else None

            if not isinstance(record, dict):
                continue

            overlap_score = calculate_patch_overlap_score(patch, record)
            if overlap_score < 0.05:
                continue

            touched_for_file.append(
                TouchedRecord(
                    uuid=candidate_uuid,
                    record=record,
                    file_path=file_path,
                    evidence="file-search-overlap",
                    overlap_score=overlap_score,
                )
            )
            unique_seen.add(candidate_uuid)

        if touched_for_file:
            touched_for_file.sort(
                key=lambda entry: (
                    0 if entry.evidence == "uuid-in-diff" else 1,
                    -(entry.overlap_score or 0.0),
                    entry.uuid,
                )
            )
            touched_by_file[file_path] = touched_for_file

    comment_body = build_comment_body(
        pr_number=config.pr_number,
        event_name=config.event_name,
        files=files,
        touched_by_file=touched_by_file,
    )

    github_api.upsert_pr_comment(config.pr_number, comment_body)

    print(
        f"Provenance review complete. files={len(files)} touched_records={sum(len(v) for v in touched_by_file.values())}"
    )
    return 0


def load_env_config() -> EnvConfig:
    repository = require_env("GITHUB_REPOSITORY")
    github_token = require_env("GITHUB_TOKEN")
    backend_base_url = require_env("BACKEND_API_BASE_URL").rstrip("/")
    backend_jwt = require_env("BACKEND_API_JWT")

    event_path = require_env("GITHUB_EVENT_PATH")
    event_name = os.getenv("GITHUB_EVENT_NAME", "pull_request")

    with open(event_path, "r", encoding="utf-8") as handle:
        event_payload = json.load(handle)

    pr = event_payload.get("pull_request")
    if not isinstance(pr, dict) or "number" not in pr:
        raise RuntimeError("GITHUB_EVENT_PATH does not contain pull_request payload.")

    pr_number = int(pr["number"])
    workspace_id = optional_env("PROVENANCE_WORKSPACE_ID")

    search_limit_raw = optional_env("PROVENANCE_SEARCH_LIMIT")
    if search_limit_raw:
        try:
            search_limit = max(1, min(200, int(search_limit_raw)))
        except ValueError:
            search_limit = DEFAULT_SEARCH_LIMIT
    else:
        search_limit = DEFAULT_SEARCH_LIMIT

    return EnvConfig(
        repository=repository,
        pr_number=pr_number,
        event_name=event_name,
        github_token=github_token,
        backend_base_url=backend_base_url,
        backend_jwt=backend_jwt,
        workspace_id=workspace_id,
        search_limit=search_limit,
    )


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value if value else None


class GitHubApi:
    def __init__(self, repository: str, token: str) -> None:
        self.repository = repository
        self.token = token
        self.base_url = "https://api.github.com"

    def list_pull_request_files(self, pr_number: int) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = 1

        while True:
            endpoint = f"/repos/{self.repository}/pulls/{pr_number}/files?per_page=100&page={page}"
            payload = self._request_json("GET", endpoint)
            if not isinstance(payload, list) or not payload:
                break

            files.extend(item for item in payload if isinstance(item, dict))
            if len(payload) < 100:
                break
            page += 1

        return files

    def upsert_pr_comment(self, pr_number: int, body: str) -> None:
        comments = self._request_json(
            "GET", f"/repos/{self.repository}/issues/{pr_number}/comments?per_page=100"
        )

        existing_comment_id: int | None = None
        if isinstance(comments, list):
            for comment in comments:
                if not isinstance(comment, dict):
                    continue

                comment_body = str(comment.get("body") or "")
                if COMMENT_MARKER in comment_body:
                    existing_comment_id = int(comment.get("id"))
                    break

        if existing_comment_id is None:
            self._request_json(
                "POST",
                f"/repos/{self.repository}/issues/{pr_number}/comments",
                {"body": body},
            )
        else:
            self._request_json(
                "PATCH",
                f"/repos/{self.repository}/issues/comments/{existing_comment_id}",
                {"body": body},
            )

    def _request_json(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        url = self.base_url + endpoint

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "provenance-review-bot",
        }

        raw = http_request(method, url, headers=headers, payload=payload)

        if not raw:
            return None

        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise HttpError(f"Failed to decode GitHub API response from {endpoint}: {error}") from error


class BackendApi:
    def __init__(self, base_url: str, jwt_token: str, workspace_id: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.jwt_token = jwt_token
        self.workspace_id = workspace_id

    def get_provenance(self, block_uuid: str) -> dict[str, Any] | None:
        endpoint = f"{self.base_url}/provenance/{block_uuid}"
        headers = self._auth_headers()

        raw = http_request("GET", endpoint, headers=headers, payload=None, tolerate_404=True)
        if raw is None:
            return None

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None

        if isinstance(payload, dict):
            record = payload.get("record")
            if isinstance(record, dict):
                return record

        return None

    def search_by_file(self, file_path: str, limit: int) -> list[dict[str, Any]]:
        endpoint = f"{self.base_url}/search"
        payload: dict[str, Any] = {
            "filePath": file_path,
            "limit": limit,
            "query": "",
        }

        if self.workspace_id:
            payload["workspaceId"] = self.workspace_id

        raw = http_request("POST", endpoint, headers=self._auth_headers(), payload=payload)
        if not raw:
            return []

        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return []

        if isinstance(decoded, dict):
            results = decoded.get("results")
            if isinstance(results, list):
                return [entry for entry in results if isinstance(entry, dict)]

        if isinstance(decoded, list):
            return [entry for entry in decoded if isinstance(entry, dict)]

        return []

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.jwt_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


def http_request(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    tolerate_404: bool = False,
) -> str | None:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None

    request = url_request.Request(url=url, data=body, method=method, headers=headers)

    try:
        with url_request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except url_error.HTTPError as error:
        if tolerate_404 and error.code == 404:
            return None

        detail = ""
        try:
            detail = error.read().decode("utf-8")
        except Exception:
            detail = ""

        raise HttpError(f"HTTP {error.code} for {url}: {detail}") from error
    except url_error.URLError as error:
        raise HttpError(f"Request failed for {url}: {error}") from error


def find_uuids_in_patch(patch: str) -> set[str]:
    if not patch:
        return set()

    return {match.group(0).lower() for match in UUID_PATTERN.finditer(patch)}


def extract_uuid(item: dict[str, Any]) -> str | None:
    for key_path in (
        ["uuid"],
        ["id"],
        ["requestUuid"],
        ["record", "uuid"],
        ["record", "id"],
    ):
        value = pick(item, key_path)
        if isinstance(value, str):
            match = UUID_PATTERN.search(value)
            if match:
                return match.group(0).lower()

    return None


def extract_record(item: dict[str, Any]) -> dict[str, Any] | None:
    if "record" in item and isinstance(item["record"], dict):
        return item["record"]

    if "insertedText" in item or "contextSnapshot" in item or "insertion" in item:
        return item

    return None


def calculate_patch_overlap_score(patch: str, record: dict[str, Any]) -> float:
    patch_tokens = tokenize_for_overlap(extract_patch_content(patch))
    if not patch_tokens:
        return 0.0

    inserted_code = (
        pick(record, ["insertion", "extractedInsertedCodeBlock"])
        or record.get("insertedText")
        or record.get("inserted_code")
        or ""
    )

    if not isinstance(inserted_code, str) or not inserted_code.strip():
        return 0.0

    code_tokens = tokenize_for_overlap(inserted_code)
    if not code_tokens:
        return 0.0

    overlap = patch_tokens.intersection(code_tokens)
    return len(overlap) / max(1, len(code_tokens))


def extract_patch_content(patch: str) -> str:
    if not patch:
        return ""

    lines = []
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue

        if line.startswith("+") or line.startswith("-"):
            lines.append(line[1:])
        else:
            lines.append(line)

    return "\n".join(lines)


def tokenize_for_overlap(text: str) -> set[str]:
    if not text:
        return set()

    return {token.lower() for token in TOKEN_PATTERN.findall(text)}


def pick(data: dict[str, Any], path: list[str]) -> Any:
    cursor: Any = data
    for segment in path:
        if not isinstance(cursor, dict) or segment not in cursor:
            return None
        cursor = cursor[segment]
    return cursor


def build_comment_body(
    *,
    pr_number: int,
    event_name: str,
    files: list[dict[str, Any]],
    touched_by_file: dict[str, list[TouchedRecord]],
) -> str:
    total_touched = sum(len(entries) for entries in touched_by_file.values())
    timestamp = datetime.now(timezone.utc).isoformat()

    lines: list[str] = [
        COMMENT_MARKER,
        "## AI Provenance Review",
        f"- Pull request: #{pr_number}",
        f"- Event: {event_name}",
        f"- Files analyzed: {len(files)}",
        f"- AI-generated blocks touched: {total_touched}",
        f"- Generated at: {timestamp}",
        "",
    ]

    if total_touched == 0:
        lines.extend(
            [
                "No touched AI-generated blocks were identified from this diff based on UUID evidence or file-level provenance overlap.",
                "",
                "If this seems incorrect, ensure provenance UUIDs are present in changed context or that backend search indexing is up to date.",
            ]
        )
        return "\n".join(lines)

    for file_path in sorted(touched_by_file.keys()):
        lines.append(f"### {file_path}")
        records = touched_by_file[file_path]

        for entry in records:
            model_name = extract_model_name(entry.record)
            timestamp_iso = extract_timestamp(entry.record)
            prompt_preview = extract_prompt_preview(entry.record)
            lineage_preview = extract_lineage_preview(entry.record)

            lines.append(f"- UUID: {entry.uuid}")
            lines.append(f"  - Evidence: {entry.evidence}")
            if entry.overlap_score is not None:
                lines.append(f"  - Patch overlap score: {entry.overlap_score:.3f}")
            lines.append(f"  - Model: {model_name}")
            lines.append(f"  - Timestamp: {timestamp_iso}")
            lines.append("  - Prompt excerpt:")
            lines.append("    > " + prompt_preview.replace("\n", "\n    > "))
            lines.append("  - Lineage summary:")
            if lineage_preview:
                for lineage_entry in lineage_preview:
                    lines.append("    - " + lineage_entry)
            else:
                lines.append("    - No lineage chain available in record payload.")

        lines.append("")

    return "\n".join(lines)


def extract_model_name(record: dict[str, Any]) -> str:
    model = (
        pick(record, ["prompt", "modelName"])
        or pick(record, ["provenance", "modelName"])
        or record.get("model")
        or record.get("modelName")
    )
    return str(model) if model else "unknown"


def extract_timestamp(record: dict[str, Any]) -> str:
    timestamp = (
        record.get("timestampIso")
        or record.get("insertionTimestampIso")
        or pick(record, ["provenance", "proxyResponseTimestampIso"])
    )
    return str(timestamp) if timestamp else "unknown"


def extract_prompt_preview(record: dict[str, Any]) -> str:
    prompt_messages = (
        pick(record, ["prompt", "fullMessages"])
        or pick(record, ["provenance", "fullPromptMessages"])
        or record.get("messages")
    )

    if prompt_messages is None:
        return "No prompt messages available."

    if isinstance(prompt_messages, str):
        text = prompt_messages.strip()
    else:
        text = json.dumps(prompt_messages, default=str)

    if len(text) > MAX_PROMPT_PREVIEW_CHARS:
        text = text[:MAX_PROMPT_PREVIEW_CHARS] + " ..."

    return text


def extract_lineage_preview(record: dict[str, Any]) -> list[str]:
    chain = (
        record.get("evolutionChain")
        or record.get("versions")
        or pick(record, ["lineage", "versions"])
        or pick(record, ["lineage", "chain"])
        or []
    )

    if not isinstance(chain, list):
        return []

    preview: list[str] = []
    for index, entry in enumerate(chain[:MAX_LINEAGE_ENTRIES], start=1):
        if isinstance(entry, dict):
            relationship = (
                entry.get("relationshipType")
                or entry.get("relation")
                or entry.get("edgeType")
                or "version"
            )
            version_id = entry.get("versionId") or entry.get("id") or entry.get("uuid") or "unknown"
            commit = entry.get("commitHash") or entry.get("commit") or "n/a"
            file_path = entry.get("filePath") or entry.get("path") or "n/a"

            preview.append(
                f"#{index} {relationship} version={version_id} commit={commit} file={file_path}"
            )
        else:
            preview.append(f"#{index} {entry}")

    if len(chain) > MAX_LINEAGE_ENTRIES:
        preview.append(f"... plus {len(chain) - MAX_LINEAGE_ENTRIES} more lineage entries")

    return preview


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Provenance PR review failed: {error}", file=sys.stderr)
        sys.exit(1)
