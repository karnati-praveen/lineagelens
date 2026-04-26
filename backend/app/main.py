from contextlib import asynccontextmanager
from ipaddress import ip_address
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.api.routes.auth import router as auth_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.provenance import router as provenance_router
from app.api.routes.search import router as search_router
from app.api.routes.explain import router as explain_router
from app.api.routes.insights import router as insights_router
from app.api.routes.team import router as team_router
from app.api.routes.ws_capture import router as ws_capture_router
from app.core.config import Settings, get_settings
from app.core.rate_limit import InMemoryRateLimiter, client_identifier
from app.db.session import create_engine_from_env, create_session_factory, initialize_database
from app.services.neo4j_service import Neo4jLineageService


logger = logging.getLogger(__name__)
DEFAULT_APP_TITLE = "AI Provenance Backend"
DEFAULT_APP_VERSION = "0.1.0"


def parse_csv_env(name: str, default: str) -> list[str]:
    raw_value = os.getenv(name, default)
    entries = [entry.strip() for entry in raw_value.split(",") if entry.strip()]
    return list(dict.fromkeys(entries))


def get_runtime_settings(request: Request) -> Settings:
    current_settings = getattr(request.app.state, "settings", None)
    if isinstance(current_settings, Settings):
        return current_settings
    return get_settings()


def _is_trusted_proxy_host(host: str | None) -> bool:
    normalized = (host or "").strip()
    if not normalized:
        return False
    if normalized.lower() == "localhost":
        return True

    try:
        parsed = ip_address(normalized)
    except ValueError:
        return False

    return parsed.is_loopback or parsed.is_private


def get_client_ip(request: Request) -> str:
    peer_host = request.client.host if request.client else None
    if _is_trusted_proxy_host(peer_host):
        forwarded_for = request.headers.get("x-forwarded-for", "")
        forwarded_chain = [entry.strip() for entry in forwarded_for.split(",") if entry.strip()]
        if forwarded_chain:
            return client_identifier(forwarded_chain[0])

        real_ip = request.headers.get("x-real-ip")
        if real_ip and real_ip.strip():
            return client_identifier(real_ip.strip())

    return client_identifier(peer_host)


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

            body = await request.body()
            if len(body) > max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large."},
                )

        if not current_settings.rate_limit_enabled or request.url.path == "/health":
            return await call_next(request)

        limiter: InMemoryRateLimiter = request.app.state.rate_limiter
        client_ip = get_client_ip(request)
        key = f"http:{client_ip}"

        decision = limiter.check(
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = create_engine_from_env()
    session_factory = create_session_factory(engine)

    app.state.settings = settings
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    app.title = settings.app_title
    app.version = settings.app_version
    app.state.rate_limiter = InMemoryRateLimiter(settings.rate_limit_max_tracked_keys)
    neo4j_service: Neo4jLineageService | None = None
    app.state.neo4j_service = neo4j_service

    try:
        await initialize_database(engine)
        neo4j_service = await initialize_neo4j_service(settings)
        app.state.neo4j_service = neo4j_service
        yield
    finally:
        if neo4j_service is not None:
            await neo4j_service.close()
        await engine.dispose()


app = FastAPI(
    title=DEFAULT_APP_TITLE,
    version=DEFAULT_APP_VERSION,
    lifespan=lifespan,
)

_allowed_hosts = parse_csv_env("BACKEND_TRUSTED_HOSTS", "")
_cors_origins = parse_csv_env(
    "BACKEND_CORS_ORIGINS",
    "http://127.0.0.1:3000,http://localhost:3000",
)
_cors_origins_raw = os.getenv("BACKEND_CORS_ORIGINS", "")

app.add_middleware(RequestGuardsMiddleware)
if _allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=None,
    allow_credentials=bool(_cors_origins_raw.strip()),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    max_age=600,
)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(auth_router)
app.include_router(ingest_router)
app.include_router(provenance_router)
app.include_router(search_router)
app.include_router(explain_router)
app.include_router(insights_router)
app.include_router(team_router)
app.include_router(ws_capture_router)


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
