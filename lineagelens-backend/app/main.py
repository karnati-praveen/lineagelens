from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

from app.api.routes.analytics import router as analytics_router
from app.api.routes.audit import router as audit_router
from app.api.routes.auth import router as auth_router
from app.api.routes.bulk import router as bulk_router
from app.api.routes.comments import router as comments_router
from app.api.routes.deletion import router as deletion_router
from app.api.routes.export import router as export_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.lineage import router as lineage_router
from app.api.routes.provenance import router as provenance_router
from app.api.routes.retention import router as retention_router
from app.api.routes.saved_queries import router as saved_queries_router
from app.api.routes.search import router as search_router
from app.api.routes.explain import router as explain_router
from app.api.routes.insights import router as insights_router
from app.api.routes.tags import router as tags_router
from app.api.routes.team import router as team_router
from app.api.routes.report import router as report_router
from app.api.routes.webhooks import router as webhooks_router
from app.api.routes.workspaces import router as workspaces_router
from app.api.routes.ws_capture import router as ws_capture_router
from app.api.routes.policies import router as policies_router
from app.api.routes.permissions import router as permissions_router
from app.api.routes.alert_config import router as alert_config_router
from app.api.routes.reviews import router as reviews_router
from app.api.routes.developers import router as developers_router
from app.api.routes.api_keys import router as api_keys_router
from app.api.routes.diff import router as diff_router
from app.api.routes.github import router as github_router
from app.api.routes.quality import router as quality_router
from app.api.routes.scheduled_reports import router as scheduled_reports_router
from app.api.routes.sso import router as sso_router
from app.api.routes.setup import router as setup_router
from app.core.config import Settings, get_settings
from app.core.rate_limit import InMemoryRateLimiter, client_identifier, effective_client_ip
from app.db.session import create_engine_from_settings, create_session_factory, initialize_database
from app.services.neo4j_service import Neo4jLineageService


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


logger = logging.getLogger(__name__)
DEFAULT_APP_TITLE = "AI Provenance Backend"
DEFAULT_APP_VERSION = "0.1.0"


def get_runtime_settings(request: Request) -> Settings:
    current_settings = getattr(request.app.state, "settings", None)
    if isinstance(current_settings, Settings):
        return current_settings
    return get_settings()


def get_client_ip(request: Request) -> str:
    peer_host = request.client.host if request.client else None
    return effective_client_ip(
        peer_host,
        request.headers.get("x-forwarded-for", ""),
        request.headers.get("x-real-ip", ""),
    )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])

                def _add_header(name: bytes, value: bytes) -> None:
                    if not any(existing_name.lower() == name.lower() for existing_name, _ in headers):
                        headers.append((name, value))

                _add_header(b"x-content-type-options", b"nosniff")
                _add_header(b"x-frame-options", b"DENY")
                _add_header(b"referrer-policy", b"no-referrer")
                _add_header(b"cross-origin-opener-policy", b"same-origin")
                _add_header(b"cross-origin-resource-policy", b"same-site")
                _add_header(
                    b"permissions-policy",
                    b"geolocation=(), microphone=(), camera=(), payment=()",
                )

                app_state = scope.get("app").state if scope.get("app") is not None else None
                current_settings = getattr(app_state, "settings", None)
                if (
                    isinstance(current_settings, Settings)
                    and current_settings.app_env.strip().lower() == "production"
                ):
                    _add_header(
                        b"strict-transport-security",
                        b"max-age=31536000; includeSubDomains",
                    )

            await send(message)

        await self.app(scope, receive, send_with_headers)


_SETUP_BYPASS_PREFIXES = {"/setup", "/health", "/auth/sso/callback"}


class SetupGuardMiddleware:
    """Redirect all traffic to /setup until the first admin account exists."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "/")

        # Always allow setup and health routes through
        if any(path.startswith(p) for p in _SETUP_BYPASS_PREFIXES):
            await self.app(scope, receive, send)
            return

        app_state = scope.get("app").state if scope.get("app") is not None else None
        if app_state is None:
            await self.app(scope, receive, send)
            return

        # Fast-path: setup already complete (cached after first check or first setup POST)
        if getattr(app_state, "setup_complete", False):
            await self.app(scope, receive, send)
            return

        # Slow-path: check DB once, then cache result
        try:
            session_factory = getattr(app_state, "db_session_factory", None)
            if session_factory is not None:
                from sqlalchemy import func, select
                from app.db.models import UserAccount
                async with session_factory() as session:
                    result = await session.execute(select(func.count()).select_from(UserAccount))
                    count = result.scalar_one() or 0
                if count > 0:
                    app_state.setup_complete = True
                    await self.app(scope, receive, send)
                    return
        except Exception:
            # If DB is not ready yet, let the request through so health checks work
            await self.app(scope, receive, send)
            return

        # Setup not complete — redirect to /setup
        redirect_body = b""
        await send({
            "type": "http.response.start",
            "status": 302,
            "headers": [(b"location", b"/setup"), (b"content-length", b"0")],
        })
        await send({"type": "http.response.body", "body": redirect_body, "more_body": False})


class RequestGuardsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        current_settings = get_runtime_settings(request)

        if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            max_bytes = current_settings.http_max_body_bytes
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": "Request body too large."},
                        )
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Invalid Content-Length header."},
                    )

        if not current_settings.rate_limit_enabled or request.url.path == "/health":
            return await call_next(request)

        limiter = request.app.state.rate_limiter
        client_ip = get_client_ip(request)
        key = f"http:{client_ip}"

        decision = await limiter.acheck(
            key=key,
            limit=current_settings.rate_limit_max_requests,
            window_seconds=current_settings.rate_limit_window_seconds,
        )

        if not decision.allowed:
            logger.warning(
                "HTTP rate limit exceeded: client=%s path=%s",
                client_ip,
                request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded.",
                    "retryAfterSeconds": decision.retry_after_seconds,
                },
                headers={
                    "Retry-After": str(decision.retry_after_seconds),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response


class StreamingBodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        if method in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return

        app_state = scope.get("app").state if scope.get("app") is not None else None
        current_settings = getattr(app_state, "settings", None)
        max_bytes = (
            current_settings.http_max_body_bytes
            if isinstance(current_settings, Settings)
            else get_settings().http_max_body_bytes
        )
        seen_bytes = 0
        rejected = False
        rejection_sent = False

        async def send_rejection() -> None:
            nonlocal rejection_sent
            if rejection_sent:
                return
            rejection_sent = True
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"Request body too large."}',
                    "more_body": False,
                }
            )

        async def receive_with_limit():
            nonlocal seen_bytes, rejected
            if rejected:
                return {"type": "http.disconnect"}

            message = await receive()
            if message["type"] != "http.request":
                return message

            body = message.get("body", b"")
            seen_bytes += len(body)
            if seen_bytes > max_bytes:
                rejected = True
                await send_rejection()
                return {"type": "http.disconnect"}

            return message

        async def send_unless_rejected(message):
            if rejected:
                return
            await send(message)

        await self.app(scope, receive_with_limit, send_unless_rejected)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Structured JSON logging when LOG_FORMAT=json
    if os.environ.get("LOG_FORMAT", "").strip().lower() == "json":
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            handler.setFormatter(JsonLogFormatter())
        if not root_logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(JsonLogFormatter())
            root_logger.addHandler(handler)
        logger.info("JSON log format enabled.")

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    app.state.settings = settings
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    app.title = settings.app_title
    app.version = settings.app_version

    from app.core.redis_store import RedisStore
    redis_client = None
    if settings.redis_url:
        from app.core.rate_limit_redis import RedisRateLimiter
        rate_limiter = RedisRateLimiter(settings.redis_url)
        try:
            import redis.asyncio as aioredis
            redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
            await redis_client.ping()
            logger.info("Redis KV store connected.")
        except Exception as exc:
            logger.warning("Redis KV store unavailable, using in-process fallback: %s", exc)
            redis_client = None
        logger.info("Redis-backed rate limiter initialised.")
    else:
        rate_limiter = InMemoryRateLimiter(settings.rate_limit_max_tracked_keys)
    app.state.rate_limiter = rate_limiter
    app.state.kv_store = RedisStore(redis_client)

    neo4j_service: Neo4jLineageService | None = None
    app.state.neo4j_service = neo4j_service

    try:
        await initialize_database(engine)
        neo4j_service = await initialize_neo4j_service(settings)
        app.state.neo4j_service = neo4j_service

        from app.services.report_scheduler import start_scheduler, stop_scheduler
        start_scheduler(session_factory)

        yield
    finally:
        from app.services.report_scheduler import stop_scheduler
        await stop_scheduler()
        if neo4j_service is not None:
            await neo4j_service.close()
        if hasattr(rate_limiter, "close"):
            await rate_limiter.close()
        if redis_client is not None:
            await redis_client.aclose()
        await engine.dispose()


app = FastAPI(
    title=DEFAULT_APP_TITLE,
    version=DEFAULT_APP_VERSION,
    lifespan=lifespan,
)

_startup_settings = get_settings()
_allowed_hosts = _startup_settings.trusted_hosts
_cors_origins = _startup_settings.cors_origins

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(SetupGuardMiddleware)
app.add_middleware(StreamingBodyLimitMiddleware)
app.add_middleware(RequestGuardsMiddleware)
if _allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=None,
    allow_credentials=bool(_cors_origins) and "*" not in _cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-API-Version", "X-Trace-ID", "X-Idempotency-Key"],
    max_age=600,
)

app.include_router(auth_router)
app.include_router(ingest_router)
app.include_router(lineage_router)
app.include_router(provenance_router)
app.include_router(deletion_router)
app.include_router(search_router)
app.include_router(explain_router)
app.include_router(insights_router)
app.include_router(export_router)
app.include_router(team_router)
app.include_router(report_router)
app.include_router(webhooks_router)
app.include_router(workspaces_router)
app.include_router(ws_capture_router)
app.include_router(audit_router)
app.include_router(analytics_router)
app.include_router(bulk_router)
app.include_router(comments_router)
app.include_router(tags_router)
app.include_router(saved_queries_router)
app.include_router(retention_router)
app.include_router(policies_router)
app.include_router(permissions_router)
app.include_router(alert_config_router)
app.include_router(reviews_router)
app.include_router(developers_router)
app.include_router(api_keys_router)
app.include_router(diff_router)
app.include_router(github_router)
app.include_router(quality_router)
app.include_router(scheduled_reports_router)
app.include_router(sso_router)
app.include_router(setup_router)


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(os.path.join(_STATIC_DIR, "dashboard.html"), media_type="text/html")


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    current_settings = get_runtime_settings(request)
    body: dict[str, object] = {
        "status": "ok",
        "app": current_settings.app_title,
        "version": current_settings.app_version,
        # productMode is always included so the VS Code extension can detect
        # which tier is running and hide unavailable UI controls accordingly.
        "productMode": current_settings.product_mode,
    }
    if current_settings.app_env.strip().lower() != "production":
        body.update(
            {
                "environment": current_settings.app_env,
                "backendMode": current_settings.backend_mode,
                "features": {
                    "neo4j": current_settings.neo4j_enabled,
                    "vectorSearch": current_settings.vector_search_enabled,
                    "lineageStrictMode": current_settings.lineage_strict_mode,
                },
            }
        )
    return body


async def initialize_neo4j_service(settings: Settings) -> Neo4jLineageService | None:
    if not settings.neo4j_enabled:
        logger.info("Neo4j is disabled; starting backend without graph lineage.")
        return None

    neo4j_service: Neo4jLineageService | None = None

    try:
        neo4j_service = Neo4jLineageService(settings)
        await neo4j_service.ensure_constraints()
        logger.info("Neo4j lineage initialized successfully.")
        return neo4j_service
    except Exception as error:
        if neo4j_service is not None:
            try:
                await neo4j_service.close()
            except Exception:  # pragma: no cover - defensive cleanup
                logger.debug("Failed to close partially initialized Neo4j service.")

        if settings.lineage_strict_mode:
            logger.exception("Neo4j initialization failed in strict lineage mode.")
            raise

        logger.warning("Neo4j is unavailable; continuing without lineage: %s", error)
        return None
