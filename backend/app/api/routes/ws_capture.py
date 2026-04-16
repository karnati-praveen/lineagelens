import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.rate_limit import InMemoryRateLimiter, client_identifier
from app.core.security import AuthError, authenticate_websocket, ensure_workspace_scope
from app.db.session import AsyncSessionLocal
from app.schemas.provenance import IngestRequest
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


def get_ws_neo4j_service(websocket: WebSocket) -> Neo4jLineageService:
    neo4j_service = getattr(websocket.app.state, "neo4j_service", None)
    if not isinstance(neo4j_service, Neo4jLineageService):
        raise RuntimeError("Neo4j service is not available for websocket capture route.")
    return neo4j_service


def get_ws_session_factory() -> async_sessionmaker[AsyncSession]:
    return AsyncSessionLocal


def get_ws_rate_limiter(websocket: WebSocket) -> InMemoryRateLimiter:
    limiter = getattr(websocket.app.state, "rate_limiter", None)
    if not isinstance(limiter, InMemoryRateLimiter):
        raise RuntimeError("Rate limiter is not available for websocket capture route.")
    return limiter


@router.websocket("/ws/capture")
async def ws_capture(
    websocket: WebSocket,
    settings: Annotated[Settings, Depends(get_ws_settings)],
    neo4j_service: Annotated[Neo4jLineageService, Depends(get_ws_neo4j_service)],
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_ws_session_factory)],
    rate_limiter: Annotated[InMemoryRateLimiter, Depends(get_ws_rate_limiter)],
) -> None:
    """Receive real-time provenance capture events from the VS Code extension.

    On each incoming event payload:
    1) Validate JWT and workspace scope.
    2) Parse and validate provenance payload.
    3) Run embedding generation + AST normalization via service layer.
    4) Persist in Postgres (including pgvector embedding).
    5) Create initial lineage nodes in Neo4j.
    6) Broadcast confirmation to all workspace subscribers.
    """

    client_ip = client_identifier(websocket.client.host if websocket.client else None)

    if settings.rate_limit_enabled:
        connection_decision = rate_limiter.check(
            key=f"ws-connect:{client_ip}",
            limit=settings.rate_limit_ws_max_connections,
            window_seconds=settings.rate_limit_ws_window_seconds,
        )

        if not connection_decision.allowed:
            logger.warning(
                "WebSocket connection rate limit exceeded: client=%s",
                client_ip,
            )
            await websocket.close(code=4429, reason="WebSocket rate limit exceeded")
            return

    auth = None
    try:
        auth = await authenticate_websocket(websocket, settings)
    except AuthError as error:
        logger.warning("WebSocket capture auth failed: %s", error)
        await websocket.close(code=4401, reason=str(error))
        return
    except Exception as error:  # pragma: no cover - defensive guard
        logger.exception("Unexpected error during websocket auth: %s", error)
        await websocket.close(code=1011, reason="Authentication failure")
        return

    await manager.connect(auth.workspace_id, websocket)
    logger.info(
        "WebSocket capture connected: workspace=%s subject=%s",
        auth.workspace_id,
        auth.subject,
    )

    await websocket.send_json(
        {
            "type": "capture.connected",
            "workspaceId": auth.workspace_id,
            "serverTime": datetime.now(tz=UTC).isoformat(),
        }
    )

    try:
        while True:
            if settings.rate_limit_enabled:
                message_decision = rate_limiter.check(
                    key=f"ws-message:{auth.workspace_id}:{auth.subject}",
                    limit=settings.rate_limit_ws_max_messages,
                    window_seconds=settings.rate_limit_ws_window_seconds,
                )

                if not message_decision.allowed:
                    logger.warning(
                        "WebSocket message rate limit exceeded: workspace=%s subject=%s",
                        auth.workspace_id,
                        auth.subject,
                    )
                    await send_error(
                        websocket,
                        error_message="WebSocket rate limit exceeded.",
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        details={"retryAfterSeconds": message_decision.retry_after_seconds},
                    )
                    break

            raw_message = await websocket.receive_text()

            if len(raw_message.encode("utf-8")) > settings.ws_max_message_bytes:
                logger.warning(
                    "WebSocket payload too large: workspace=%s subject=%s",
                    auth.workspace_id,
                    auth.subject,
                )
                await send_error(
                    websocket,
                    error_message="WebSocket message too large.",
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    details={"maxBytes": settings.ws_max_message_bytes},
                )
                break

            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError as error:
                logger.info("Invalid JSON websocket message: %s", error)
                await send_error(
                    websocket,
                    error_message="Message must be valid JSON.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
                continue

            if not isinstance(message, dict):
                await send_error(
                    websocket,
                    error_message="Message payload must be a JSON object.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
                continue

            message_type = str(message.get("type", "ingest")).strip().lower()
            if message_type in {"ping", "heartbeat"}:
                await websocket.send_json(
                    {
                        "type": "capture.pong",
                        "serverTime": datetime.now(tz=UTC).isoformat(),
                    }
                )
                continue

            if message_type not in {"ingest", "capture.ingest"}:
                await send_error(
                    websocket,
                    error_message="Unsupported websocket message type.",
                    details={"type": message_type},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
                continue

            raw_payload = message.get("payload")
            if raw_payload is None:
                raw_payload = message

            if not isinstance(raw_payload, dict):
                await send_error(
                    websocket,
                    error_message="Payload must be a JSON object.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
                continue

            try:
                payload = IngestRequest.model_validate(raw_payload)
            except ValidationError as error:
                logger.info("Invalid ingest payload received: %s", error)
                await send_error(
                    websocket,
                    error_message="Invalid ingest payload.",
                    details={"validation": error.errors()},
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
                continue

            try:
                ensure_workspace_scope(auth, payload.workspace_id)
            except Exception as error:
                logger.warning(
                    "Workspace scope mismatch on websocket capture: workspace=%s payload_workspace=%s",
                    auth.workspace_id,
                    payload.workspace_id,
                )
                await send_error(
                    websocket,
                    error_message=str(error),
                    status_code=status.HTTP_403_FORBIDDEN,
                )
                continue

            try:
                async with session_factory() as session:
                    record = await ingest_provenance_event(
                        session=session,
                        payload=payload,
                        auth=auth,
                        settings=settings,
                        neo4j_service=neo4j_service,
                    )
            except Exception:
                logger.exception(
                    "Failed to ingest websocket payload: workspace=%s request_uuid=%s file=%s",
                    auth.workspace_id,
                    payload.request_uuid,
                    payload.file_path,
                )
                await send_error(
                    websocket,
                    error_message="Failed to ingest event.",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
                continue

            logger.info(
                "WebSocket ingest stored: workspace=%s uuid=%s file=%s",
                auth.workspace_id,
                record.uuid,
                record.file_path,
            )

            confirmation = {
                "type": "capture.confirmed",
                "workspaceId": auth.workspace_id,
                "uuid": str(record.uuid),
                "requestUuid": str(record.request_uuid) if record.request_uuid else None,
                "filePath": record.file_path,
                "timestampIso": record.timestamp_iso.isoformat(),
                "lineageNodeId": record.lineage_node_id,
                "embeddingModel": record.embedding_model,
                "status": status.HTTP_201_CREATED,
            }

            if not await safe_send_json(websocket, confirmation):
                logger.warning("Unable to send capture confirmation to websocket client.")
                break

            await manager.broadcast(
                auth.workspace_id,
                {
                    "type": "capture.update",
                    "event": "ingested",
                    **confirmation,
                },
            )

    except WebSocketDisconnect:
        logger.info(
            "WebSocket capture disconnected: workspace=%s subject=%s",
            auth.workspace_id,
            auth.subject,
        )
    except Exception as error:
        logger.exception("Unhandled websocket capture error: %s", error)
        try:
            await websocket.close(code=1011, reason=str(error))
        except RuntimeError:
            # Socket may already be closed by the peer.
            pass
    finally:
        if auth is not None:
            manager.disconnect(auth.workspace_id, websocket)


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
