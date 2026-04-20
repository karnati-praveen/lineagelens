from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

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
from app.db.session import engine, initialize_database
from app.services.neo4j_service import Neo4jLineageService


settings: Settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = settings
    app.state.rate_limiter = InMemoryRateLimiter(settings.rate_limit_max_tracked_keys)
    app.state.neo4j_service = None

    await initialize_database()

    neo4j_service = await initialize_neo4j_service(settings)
    app.state.neo4j_service = neo4j_service

    try:
        yield
    finally:
        if neo4j_service is not None:
            await neo4j_service.close()
        await engine.dispose()


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    lifespan=lifespan,
)

_allowed_hosts = settings.trusted_hosts
if _allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

_cors_origins = settings.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=None,
    allow_credentials=bool(_cors_origins),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    max_age=600,
)


@app.middleware("http")
async def apply_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=(), payment=()",
    )
    if request.app.state.settings.app_env.strip().lower() == "production":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


@app.middleware("http")
async def enforce_http_payload_size(request: Request, call_next):
    current_settings: Settings = request.app.state.settings

    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return await call_next(request)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > current_settings.http_max_body_bytes:
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
    if len(body) > current_settings.http_max_body_bytes:
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body too large."},
        )

    async def receive() -> dict[str, object]:
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    request = Request(request.scope, receive)
    return await call_next(request)


@app.middleware("http")
async def enforce_http_rate_limit(request: Request, call_next):
    current_settings: Settings = request.app.state.settings
    if not current_settings.rate_limit_enabled:
        return await call_next(request)

    if request.url.path == "/health":
        return await call_next(request)

    limiter: InMemoryRateLimiter = request.app.state.rate_limiter

    client_ip = client_identifier(request.client.host if request.client else None)
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

app.include_router(auth_router)
app.include_router(ingest_router)
app.include_router(provenance_router)
app.include_router(search_router)
app.include_router(explain_router)
app.include_router(insights_router)
app.include_router(team_router)
app.include_router(ws_capture_router)


@app.get("/health")
async def health() -> dict[str, object]:
    body: dict[str, object] = {
        "status": "ok",
        "app": settings.app_title,
        "version": settings.app_version,
    }
    if settings.app_env.strip().lower() != "production":
        body.update(
            {
                "environment": settings.app_env,
                "productMode": settings.product_mode,
                "backendMode": settings.backend_mode,
                "features": {
                    "neo4j": settings.neo4j_enabled,
                    "vectorSearch": settings.vector_search_enabled,
                    "lineageStrictMode": settings.lineage_strict_mode,
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
