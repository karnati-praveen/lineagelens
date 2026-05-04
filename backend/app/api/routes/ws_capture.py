import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID as PyUUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.rate_limit import InMemoryRateLimiter, effective_client_ip
from app.core.security import AuthContext, AuthError, authenticate_websocket, ensure_workspace_scope
from app.db.models import UserAccount
from app.db.session import get_session_factory_from_app
from app.services.ingest_normalizer import extract_workspace_id, normalize_ingest_payload
from app.services.neo4j_service import Neo4jLineageService
from app.services.provenance_service import ingest_provenance_event
from app.services.websocket_manager import WebSocketConnectionManager


router = APIRouter(tags=["capture"])
manager = WebSocketConnectionManager()
logger = logging.getLogger(__name__)


def get_ws_settings(websocket: WebSocket) -> Settings:
    settings = getattr(websocket.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("Application settings are not available for websocket capture route.")
    return settings


def get_ws_neo4j_service(websocket: WebSocket) -> Neo4jLineageService | None:
    neo4j_service = getattr(websocket.app.state, "neo4j_service", None)
    if neo4j_service is not None and not isinstance(neo4j_service, Neo4jLineageService):
        raise RuntimeError("Neo4j service is not available for websocket capture route.")
    return neo4j_service


def get_ws_session_factory(websocket: WebSocket) -> async_sessionmaker[AsyncSession]:
    return get_session_factory_from_app(websocket.app)


def get_ws_rate_limiter(websocket: WebSocket) -> InMemoryRateLimiter:
    limiter = getattr(websocket.app.state, "rate_limiter", None)
    if not isinstance(limiter, InMemoryRateLimiter):
        raise RuntimeError("Rate limiter is not available for websocket capture route.")
    return limiter


async def _verify_ws_token_against_db(
    auth: AuthContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Raise HTTPException if the token has been revoked or the account is inactive."""
    # All tokens issued by this backend include token_version; absence means a crafted token.
    try:
        token_version_claim = int(auth.token_payload.get("token_version", -1))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token_version claim.",
        )

    if token_version_claim < 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is missing version claim.",
        )

    try:
        user_id = PyUUID(auth.subject)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject.",
        )

    async with session_factory() as session:
        result = await session.execute(
            select(UserAccount.token_version, UserAccount.is_active).where(UserAccount.id == user_id)
        )
        row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked.")

    db_token_version, is_active = row
    if not is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive.")
    if db_token_version != token_version_claim:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked.")


async def _setup_ws_connection(
    websocket: WebSocket,
    settings: Settings,
    rate_limiter: InMemoryRateLimiter,
    client_ip: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> Any | None:
    if settings.rate_limit_enabled:
        decision = rate_limiter.check(
            key=f"ws-connect:{client_ip}",
            limit=settings.rate_limit_ws_max_connections,
            window_seconds=settings.rate_limit_ws_window_seconds,
        )
        if not decision.allowed:
            logger.warning("WebSocket connection rate limit exceeded: client=%s", client_ip)
            await websocket.close(code=4429, reason="WebSocket rate limit exceeded")
            return None

    try:
        auth = authenticate_websocket(websocket, settings)
    except AuthError as error:
        logger.warning("WebSocket capture auth failed: %s", error)
        await websocket.close(code=4401, reason=str(error))
        return None
    except Exception as error:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error during websocket auth: %s", error)
        await websocket.close(code=1011, reason="Authentication failure")
        return None

    try:
        await _verify_ws_token_against_db(auth, session_factory)
    except HTTPException as error:
        logger.warning("WebSocket token revocation check failed: subject=%s detail=%s", auth.subject, error.detail)
        await websocket.close(code=4401, reason=error.detail)
        return None

    return auth


async def _process_one_ws_message(
    websocket: WebSocket,
    raw_message: str,
    auth: Any,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    neo4j_service: Neo4jLineageService | None,
) -> bool:
    """Returns False if the connection should be closed."""
    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError as error:
        logger.info("Invalid JSON websocket message: %s", error)
        await send_error(websocket, error_message="Message must be valid JSON.", status_code=status.HTTP_400_BAD_REQUEST)
        return True

    if not isinstance(message, dict):
        await send_error(websocket, error_message="Message payload must be a JSON object.", status_code=status.HTTP_400_BAD_REQUEST)
        return True

    message_type = str(message.get("type", "ingest")).strip().lower()
    if message_type in {"ping", "heartbeat"}:
        await websocket.send_json({"type": "capture.pong", "serverTime": datetime.now(tz=UTC).isoformat()})
        return True

    if message_type not in {"ingest", "capture.ingest"}:
        await send_error(websocket, error_message="Unsupported websocket message type.", details={"type": message_type}, status_code=status.HTTP_400_BAD_REQUEST)
        return True

    raw_payload = message.get("payload")
    if raw_payload is None:
        raw_payload = message

    if not isinstance(raw_payload, dict):
        await send_error(websocket, error_message="Payload must be a JSON object.", status_code=status.HTTP_400_BAD_REQUEST)
        return True

    requested_workspace: str | None = None
    try:
        requested_workspace = extract_workspace_id(raw_payload)
        ensure_workspace_scope(auth, requested_workspace)
        payload = normalize_ingest_payload(raw_payload, workspace_id=auth.workspace_id)
    except HTTPException as error:
        logger.warning(
            "Workspace scope mismatch on websocket capture: workspace=%s payload_workspace=%s",
            auth.workspace_id,
            requested_workspace,
        )
        await send_error(websocket, error_message=str(error.detail), status_code=error.status_code)
        return True
    except ValueError as error:
        logger.info("Invalid ingest payload received: %s", error)
        await send_error(websocket, error_message="Invalid ingest payload.", details={"validation": str(error)}, status_code=status.HTTP_400_BAD_REQUEST)
        return True

    try:
        async with session_factory() as session:
            outcome = await ingest_provenance_event(session=session, payload=payload, auth=auth, settings=settings, neo4j_service=neo4j_service)
    except Exception:
        logger.exception(
            "Failed to ingest websocket payload: workspace=%s request_uuid=%s file=%s",
            auth.workspace_id,
            payload.request_uuid,
            payload.file_path,
        )
        await send_error(websocket, error_message="Failed to ingest event.", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return True

    logger.info("WebSocket ingest stored: workspace=%s uuid=%s file=%s", auth.workspace_id, outcome.record.uuid, outcome.record.file_path)
    confirmation = {
        "type": "capture.confirmed",
        "workspaceId": auth.workspace_id,
        "uuid": str(outcome.record.uuid),
        "requestUuid": str(outcome.record.request_uuid) if outcome.record.request_uuid else None,
        "filePath": outcome.record.file_path,
        "timestampIso": outcome.record.timestamp_iso.isoformat(),
        "lineageNodeId": outcome.record.lineage_node_id,
        "embeddingModel": outcome.record.embedding_model,
        "warnings": outcome.warnings,
        "status": status.HTTP_201_CREATED,
    }

    if not await safe_send_json(websocket, confirmation):
        logger.warning("Unable to send capture confirmation to websocket client.")
        return False

    await manager.broadcast(auth.workspace_id, {"type": "capture.update", "event": "ingested", **confirmation})
    return True


async def _run_ws_message_loop(
    websocket: WebSocket,
    auth: Any,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    neo4j_service: Neo4jLineageService | None,
    rate_limiter: InMemoryRateLimiter,
) -> None:
    while True:
        if settings.rate_limit_enabled:
            message_decision = rate_limiter.check(
                key=f"ws-message:{auth.workspace_id}:{auth.subject}",
                limit=settings.rate_limit_ws_max_messages,
                window_seconds=settings.rate_limit_ws_window_seconds,
            )
            if not message_decision.allowed:
                logger.warning("WebSocket message rate limit exceeded: workspace=%s subject=%s", auth.workspace_id, auth.subject)
                await send_error(websocket, error_message="WebSocket rate limit exceeded.", status_code=status.HTTP_429_TOO_MANY_REQUESTS, details={"retryAfterSeconds": message_decision.retry_after_seconds})
                break

        raw_message = await websocket.receive_text()

        if len(raw_message.encode("utf-8")) > settings.ws_max_message_bytes:
            logger.warning("WebSocket payload too large: workspace=%s subject=%s", auth.workspace_id, auth.subject)
            await send_error(websocket, error_message="WebSocket message too large.", status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, details={"maxBytes": settings.ws_max_message_bytes})
            break

        if not await _process_one_ws_message(websocket, raw_message, auth, settings, session_factory, neo4j_service):
            break


@router.websocket("/ws/capture")
async def ws_capture(
    websocket: WebSocket,
    settings: Annotated[Settings, Depends(get_ws_settings)],
    neo4j_service: Annotated[Neo4jLineageService | None, Depends(get_ws_neo4j_service)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_ws_session_factory)],
    rate_limiter: Annotated[InMemoryRateLimiter, Depends(get_ws_rate_limiter)],
) -> None:
    peer_host = websocket.client.host if websocket.client else None
    client_ip = effective_client_ip(
        peer_host,
        websocket.headers.get("x-forwarded-for", ""),
        websocket.headers.get("x-real-ip", ""),
    )
    auth = await _setup_ws_connection(websocket, settings, rate_limiter, client_ip, session_factory)
    if auth is None:
        return

    await manager.connect(auth.workspace_id, websocket)
    logger.info("WebSocket capture connected: workspace=%s subject=%s", auth.workspace_id, auth.subject)
    await websocket.send_json({"type": "capture.connected", "workspaceId": auth.workspace_id, "serverTime": datetime.now(tz=UTC).isoformat()})

    try:
        await _run_ws_message_loop(websocket, auth, settings, session_factory, neo4j_service, rate_limiter)
    except WebSocketDisconnect:
        logger.info("WebSocket capture disconnected: workspace=%s subject=%s", auth.workspace_id, auth.subject)
    except Exception as error:
        logger.exception("Unhandled websocket capture error: %s", error)
        try:
            await websocket.close(code=1011, reason=str(error))
        except RuntimeError:
            pass
    finally:
        if auth is not None:
            await manager.disconnect(auth.workspace_id, websocket)


async def send_error(
    websocket: WebSocket,
    *,
    error_message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> bool:
    payload: dict[str, Any] = {
        "type": "capture.error",
        "error": error_message,
        "status": status_code,
        "serverTime": datetime.now(tz=UTC).isoformat(),
    }

    if details:
        payload["details"] = details

    return await safe_send_json(websocket, payload)


async def safe_send_json(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except Exception as error:
        logger.warning("Failed to send websocket message: %s", error)
        return False
