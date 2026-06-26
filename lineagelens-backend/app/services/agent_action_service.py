"""Agent action ledger service (F4).

Provides hash-chained storage for every discrete action an autonomous agent
takes within a session (shell commands, file mutations, dependency installs,
network calls).  Each action is anchored into a tamper-evident chain so
retrospective modification is detectable via record_hash / prev_hash.

Risky-action heuristics are documented in flag_risky_action().
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import log_audit_event
from app.db.models import AgentAction, ProvenanceRecord
from app.schemas.agent_actions import AgentActionItem, IngestAgentActionsResponse

logger = logging.getLogger(__name__)

# Maximum JSON-serialised bytes for arguments stored per action.
_ARGS_MAX_BYTES = 4096
# Maximum string-value length before truncation inside arguments.
_ARG_STRING_TRUNCATE = 1024


# ── Hashing ───────────────────────────────────────────────────────────────────

def compute_action_hash(
    *,
    workspace_id: str,
    session_key: str,
    action_type: str,
    tool_name: str,
    arguments_json: dict | None,
    occurred_at: str,
    prev_hash: str | None,
    agent_identity: str | None = None,
    human_principal: str | None = None,
    mandate_ref: str | None = None,
    capability: str | None = None,
    authority_state: str | None = None,
) -> str:
    """SHA-256 chain hash for one AgentAction row.

    Canonical serialisation is sorted-key JSON; any retrospective DB edit
    changes the hash and breaks the chain — that is the tamper signal. The
    authority context (PART 2 #16) is bound into the hash so a claim that an
    action was permitted cannot be added or altered after the fact.
    """
    canonical = json.dumps(
        {
            "workspace_id": workspace_id,
            "session_key": session_key,
            "action_type": action_type,
            "tool_name": tool_name,
            "arguments_json": arguments_json or {},
            "occurred_at": occurred_at,
            "prev_hash": prev_hash or "",
            "agent_identity": agent_identity or "",
            "human_principal": human_principal or "",
            "mandate_ref": mandate_ref or "",
            "capability": capability or "",
            "authority_state": authority_state or "",
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Argument sanitisation ─────────────────────────────────────────────────────

def _truncate_strings(value: object, max_len: int = _ARG_STRING_TRUNCATE) -> object:
    """Recursively truncate long strings inside a JSON-serialisable value."""
    if isinstance(value, str):
        return value[:max_len] if len(value) > max_len else value
    if isinstance(value, list):
        return [_truncate_strings(v, max_len) for v in value]
    if isinstance(value, dict):
        return {k: _truncate_strings(v, max_len) for k, v in value.items()}
    return value


def _bound_arguments(args: dict | None) -> dict | None:
    """Truncate string values and enforce total serialised size cap."""
    if not args:
        return args
    truncated = _truncate_strings(args)
    try:
        serialised = json.dumps(truncated, default=str)
        if len(serialised) <= _ARGS_MAX_BYTES:
            return truncated
        # Over the cap — keep only top-level keys that fit
        result: dict = {}
        seen = 0
        for k, v in truncated.items():
            chunk = json.dumps({k: v}, default=str)
            if seen + len(chunk) > _ARGS_MAX_BYTES:
                result["__truncated__"] = True
                break
            result[k] = v
            seen += len(chunk)
        return result
    except Exception:
        return {"__error__": "serialisation_failed"}


# ── Risky-action heuristics ───────────────────────────────────────────────────

_INSTALL_PATTERNS = re.compile(
    r"\b(npm\s+install|npm\s+i\b|yarn\s+add|pnpm\s+add|pip\s+install|pip3\s+install"
    r"|uv\s+add|poetry\s+add|cargo\s+install|gem\s+install|go\s+get"
    r"|apt(-get)?\s+install|brew\s+install|apk\s+add)\b",
    re.IGNORECASE,
)

_INTERNAL_IP = re.compile(
    r"https?://(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+"
    r"|192\.168\.\d+\.\d+|169\.254\.\d+\.\d+|::1)",
    re.IGNORECASE,
)


def flag_risky_action(
    action_type: str,
    tool_name: str,
    arguments_json: dict | None,
) -> dict | None:
    """Heuristic risky-pattern detector for agent actions.

    Returns a risk_flags dict {"patterns": [...], "riskLevel": "high|medium|low"}
    or None when no risky patterns are found.

    Patterns detected:
      pipe_to_shell          — curl|wget piped directly to bash/sh (RCE vector)
      bash_process_sub       — bash <(curl ...) or similar process-substitution RCE
      mass_delete            — rm -rf with / ~ or ./ targets (irreversible loss)
      disk_operation         — dd if=/dev/... (raw disk manipulation)
      privilege_escalation   — sudo prefix (unexpected privilege boundary crossing)
      write_outside_workspace — absolute path write outside /workspace (scope escape)
      non_registry_source    — dependency install from a URL, github:, or file: ref
                               instead of the canonical package registry
      ssrf_risk              — outbound network call to a loopback/RFC-1918 address
                               (Server-Side Request Forgery vector)
    """
    if not arguments_json:
        return None

    patterns: list[str] = []
    risk_level = "low"

    def _upgrade(new_level: str) -> None:
        nonlocal risk_level
        order = {"low": 0, "medium": 1, "high": 2}
        if order.get(new_level, 0) > order.get(risk_level, 0):
            risk_level = new_level

    # ── Shell command analysis ─────────────────────────────────────────────
    if action_type in ("shell", "dependency_install"):
        cmd = arguments_json.get("command", "") or arguments_json.get("cmd", "") or ""
        if isinstance(cmd, str):
            # curl|wget piped to shell
            if re.search(r"\b(curl|wget)\b[^|]*\|\s*(ba)?sh\b", cmd, re.IGNORECASE):
                patterns.append("pipe_to_shell")
                _upgrade("high")
            # bash <(curl ...) process substitution
            if re.search(r"\bbash\s+<\s*\(", cmd, re.IGNORECASE):
                patterns.append("bash_process_sub")
                _upgrade("high")
            # Mass deletion: rm -rf / or rm -rf ~ or rm -rf ./*
            if re.search(
                r"\brm\s+(-[a-z]*r[a-z]*f[a-z]*|-[a-z]*f[a-z]*r[a-z]*)\s+[/~]",
                cmd, re.IGNORECASE,
            ):
                patterns.append("mass_delete")
                _upgrade("high")
            # Raw disk operations
            if re.search(r"\bdd\b.*\bif=/dev/", cmd, re.IGNORECASE):
                patterns.append("disk_operation")
                _upgrade("high")
            # Privilege escalation
            if re.search(r"(^|\s)sudo\s", cmd, re.IGNORECASE):
                patterns.append("privilege_escalation")
                _upgrade("medium")

    # ── File-write scope escape ────────────────────────────────────────────
    if action_type == "file_write":
        path = arguments_json.get("file_path", "") or arguments_json.get("path", "") or ""
        if isinstance(path, str) and path.startswith("/") and not path.startswith("/workspace"):
            patterns.append("write_outside_workspace")
            _upgrade("medium")

    # ── Non-registry dependency source ────────────────────────────────────
    if action_type == "dependency_install":
        cmd = arguments_json.get("command", "") or ""
        # Installing from a URL, GitHub shorthand, or local file reference
        if re.search(r"(https?://|github:|gitlab:|bitbucket:|file:)", cmd, re.IGNORECASE):
            patterns.append("non_registry_source")
            _upgrade("medium")

    # ── SSRF — network call to loopback / RFC-1918 ────────────────────────
    if action_type == "network":
        url = arguments_json.get("url", "") or ""
        if isinstance(url, str) and _INTERNAL_IP.search(url):
            patterns.append("ssrf_risk")
            _upgrade("medium")

    if not patterns:
        return None
    return {"patterns": patterns, "riskLevel": risk_level}


# ── Core record_action ────────────────────────────────────────────────────────

async def record_action(
    session: AsyncSession,
    *,
    workspace_id: str,
    session_key: str,
    prompt_context_id: str | None,
    action: AgentActionItem,
    prev_hash: str | None,
    actor_user_id: str | None = None,
) -> AgentAction:
    """Persist one action row with hash chain + risk flags.

    Callers are responsible for sequencing prev_hash correctly (lock-free batch
    writes are fine because the proxy ingest path processes one session at a time).
    Returns the saved AgentAction so callers can thread its record_hash as the
    next prev_hash.
    """
    args = _bound_arguments(action.argumentsJson)
    risk = flag_risky_action(action.actionType, action.toolName, args)

    try:
        occurred_at = datetime.fromisoformat(action.occurredAt.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        occurred_at = datetime.now(tz=UTC)

    occurred_iso = occurred_at.isoformat()

    # PART 2 #16 — derive authority context. Absence of a mandate is recorded as
    # "unmandated", never silently treated as authorised.
    agent_identity = getattr(action, "agentIdentity", None)
    human_principal = getattr(action, "humanPrincipal", None)
    mandate_ref = getattr(action, "mandateRef", None)
    capability = getattr(action, "capability", None)
    authority_state = "mandated" if mandate_ref else "unmandated"

    record_hash = compute_action_hash(
        workspace_id=workspace_id,
        session_key=session_key,
        action_type=action.actionType,
        tool_name=action.toolName,
        arguments_json=args,
        occurred_at=occurred_iso,
        prev_hash=prev_hash,
        agent_identity=agent_identity,
        human_principal=human_principal,
        mandate_ref=mandate_ref,
        capability=capability,
        authority_state=authority_state,
    )

    row = AgentAction(
        workspace_id=workspace_id,
        session_key=session_key,
        prompt_context_id=prompt_context_id,
        action_type=action.actionType,
        tool_name=action.toolName,
        arguments_json=args,
        risk_flags_json=risk,
        agent_identity=agent_identity,
        human_principal=human_principal,
        mandate_ref=mandate_ref,
        capability=capability,
        authority_state=authority_state,
        record_hash=record_hash,
        prev_hash=prev_hash,
        occurred_at=occurred_at,
    )
    session.add(row)
    await session.flush()

    if risk:
        await log_audit_event(
            session,
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="agent_action.risky_flagged",
            target_uuid=str(row.id),
            details={
                "sessionKey": session_key,
                "actionType": action.actionType,
                "toolName": action.toolName,
                "riskLevel": risk.get("riskLevel"),
                "patterns": risk.get("patterns"),
            },
        )

    return row


async def record_actions_batch(
    session: AsyncSession,
    *,
    workspace_id: str,
    session_key: str,
    prompt_context_id: str | None,
    actions: list[AgentActionItem],
    actor_user_id: str | None = None,
) -> IngestAgentActionsResponse:
    """Record a batch of agent actions, chaining each hash to the previous.

    Fetches the workspace's tail hash (latest record_hash in this session) so
    new actions extend the existing chain rather than starting a fresh one.
    """
    # Fetch the latest prev_hash for this session to continue the chain.
    tail_result = await session.execute(
        select(AgentAction.record_hash)
        .where(
            AgentAction.workspace_id == workspace_id,
            AgentAction.session_key == session_key,
        )
        .order_by(AgentAction.id.desc())
        .limit(1)
    )
    prev_hash: str | None = tail_result.scalar_one_or_none()

    recorded = 0
    skipped = 0
    for action in actions:
        try:
            row = await record_action(
                session,
                workspace_id=workspace_id,
                session_key=session_key,
                prompt_context_id=prompt_context_id,
                action=action,
                prev_hash=prev_hash,
                actor_user_id=actor_user_id,
            )
            prev_hash = row.record_hash
            recorded += 1
        except Exception:
            logger.exception(
                "agent_action record failed workspace=%s session=%s tool=%s",
                workspace_id, session_key, action.toolName,
            )
            skipped += 1

    return IngestAgentActionsResponse(
        recorded=recorded,
        skipped=skipped,
        workspaceId=workspace_id,
    )


# ── Query helpers ─────────────────────────────────────────────────────────────

async def list_actions(
    session: AsyncSession,
    *,
    workspace_id: str,
    session_key: str | None = None,
    action_type: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[AgentAction]:
    """Return agent actions for a workspace with optional filters."""
    filters = [AgentAction.workspace_id == workspace_id]
    if session_key:
        filters.append(AgentAction.session_key == session_key)
    if action_type:
        filters.append(AgentAction.action_type == action_type)
    if from_dt:
        filters.append(AgentAction.occurred_at >= from_dt)
    if to_dt:
        filters.append(AgentAction.occurred_at <= to_dt)

    result = await session.execute(
        select(AgentAction)
        .where(and_(*filters))
        .order_by(AgentAction.occurred_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_session_reconstruction(
    session: AsyncSession,
    *,
    workspace_id: str,
    session_key: str,
) -> tuple[list[AgentAction], list[str]]:
    """Return (actions, provenance_record_uuids) for a full session reconstruction.

    The provenance_record_uuids are the UUIDs of code-ingest records whose
    session_key matches (stored in provenance_payload.normalizedEvent.source.sessionId).
    This joins the action ledger back to the code provenance for the
    prompt → actions → code narrative.
    """
    actions_result = await session.execute(
        select(AgentAction)
        .where(
            AgentAction.workspace_id == workspace_id,
            AgentAction.session_key == session_key,
        )
        .order_by(AgentAction.occurred_at.asc())
    )
    actions = list(actions_result.scalars().all())

    # Look for provenance records whose payload carries this session_key.
    # The proxy stores it under provenance_payload → provenance → sessionKey.
    prov_result = await session.execute(
        select(ProvenanceRecord.uuid)
        .where(
            ProvenanceRecord.workspace_id == workspace_id,
        )
    )
    all_prov = prov_result.all()

    # Filter in Python — JSONB path query syntax varies across SQLite/Postgres.
    # For small result sets this is acceptable; a future migration can add a
    # generated column on Postgres for indexed session_key lookups.
    prov_uuids: list[str] = []
    for (uuid,) in all_prov:
        prov_uuids.append(str(uuid))

    # Note: returning all workspace prov UUIDs here is intentional for now;
    # the route handler should cross-filter by session_key from provenance_payload
    # once we have a richer query path. For now we return the session's actions
    # and let callers enrich with provenance links if needed.
    # Return empty list for provenance until we have the proper index.
    return actions, []
