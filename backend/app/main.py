from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.auth import router as auth_router
from app.api.routes.ingest import router as ingest_router
from app.api.routes.provenance import router as provenance_router
from app.api.routes.search import router as search_router
from app.api.routes.explain import router as explain_router
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

    await initialize_database()

    neo4j_service = Neo4jLineageService(settings)
    await neo4j_service.ensure_constraints()
    app.state.neo4j_service = neo4j_service

    try:
        yield
    finally:
        await neo4j_service.close()
        await engine.dispose()


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
app.include_router(ws_capture_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "app": settings.app_title,
        "version": settings.app_version,
        "environment": settings.app_env,
    }
