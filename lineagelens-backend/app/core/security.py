from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass, replace as dc_replace
from datetime import UTC, datetime, timedelta
from uuid import UUID as PyUUID

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError, jwt
from sqlalchemy import Text, cast, exists, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db_session


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
_PBKDF2_SCHEME = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 390000
_INVALID_TOKEN_SUBJECT = "Invalid token subject."


def get_client_ip(request: Request, settings: Settings | None = None) -> str | None:
    """Return the real client IP, only trusting X-Forwarded-For from allowlisted proxies.

    This is the canonical implementation — import from here, not from app.main.
    """
    from app.core.rate_limit import effective_client_ip

    trusted_proxy_ips = getattr(settings, "trusted_proxy_ips", None) if settings is not None else None
    peer_host = request.client.host if request.client else None
    return effective_client_ip(
        peer_host,
        request.headers.get("x-forwarded-for", ""),
        request.headers.get("x-real-ip", ""),
        trusted_proxy_ips,
    ) or None


@dataclass(slots=True)
class AuthContext:
    subject: str
    workspace_id: str
    scopes: set[str]
    token_type: str
    token_payload: dict[str, object]


class AuthError(Exception):
    pass


class TokenExpiredError(AuthError):
    pass


async def get_current_auth_context(
    token: str | None = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
) -> AuthContext:
    from app.db.models import UserAccount  # local import to avoid circular dependency at module load

    if token is None or token.strip() == "":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
        )

    try:
        auth = decode_token(
            token.strip(),
            settings,
            expected_token_type="access",
            require_scopes=True,
            use_refresh_secret=False,
        )
    except AuthError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    # Verify token_version to prevent use of tokens revoked on logout or password change.
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_TOKEN_SUBJECT)

    result = await session.execute(
        select(UserAccount.token_version, UserAccount.is_active).where(UserAccount.id == user_id)
    )
    row = result.one_or_none()
    if row is None or row.token_version != token_version_claim:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked.")
    if not row.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is inactive.")

    return auth


async def get_ingest_auth_context(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
) -> AuthContext:
    static = (settings.proxy_static_token or "").strip()
    tok = (token or "").strip()
    if static and tok and hmac.compare_digest(tok, static):
        try:
            body_bytes = await request.body()
            data = json.loads(body_bytes)
            workspace_id = str(
                data.get("workspaceId") or data.get("workspace_id") or ""
            ).strip()
        except Exception:
            workspace_id = ""
        if not workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Proxy ingest payload must include workspaceId.",
            )
        return AuthContext(
            subject="proxy",
            workspace_id=workspace_id,
            scopes=settings.required_scopes_set,
            token_type="proxy",
            token_payload={},
        )
    return await get_current_auth_context(token=token, settings=settings, session=session)


def authenticate_websocket(websocket: WebSocket, settings: Settings) -> AuthContext:
    token = extract_websocket_token(websocket)

    if not token:
        raise AuthError("Missing websocket bearer token.")

    return decode_token(
        token,
        settings,
        expected_token_type="access",
        require_scopes=True,
        use_refresh_secret=False,
    )


def create_access_token(
    *,
    subject: str,
    workspace_id: str,
    scopes: list[str] | set[str] | tuple[str, ...],
    settings: Settings,
    extra_claims: dict[str, object] | None = None,
) -> tuple[str, datetime]:
    expires_at = datetime.now(tz=UTC) + timedelta(minutes=settings.jwt_access_token_ttl_minutes)

    payload: dict[str, object] = {
        "sub": subject,
        "workspace_id": workspace_id,
        "scopes": sorted({scope.strip() for scope in scopes if scope.strip()}),
        "token_type": "access",
        "iat": int(datetime.now(tz=UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    if settings.jwt_audience:
        payload["aud"] = settings.jwt_audience

    if settings.jwt_issuer:
        payload["iss"] = settings.jwt_issuer

    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_at


def create_refresh_token(
    *,
    subject: str,
    workspace_id: str,
    settings: Settings,
    extra_claims: dict[str, object] | None = None,
) -> tuple[str, datetime]:
    expires_at = datetime.now(tz=UTC) + timedelta(minutes=settings.jwt_refresh_token_ttl_minutes)

    payload: dict[str, object] = {
        "sub": subject,
        "workspace_id": workspace_id,
        "scopes": [],
        "token_type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": int(datetime.now(tz=UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    if settings.jwt_audience:
        payload["aud"] = settings.jwt_audience

    if settings.jwt_issuer:
        payload["iss"] = settings.jwt_issuer

    if extra_claims:
        payload.update(extra_claims)

    token = jwt.encode(payload, settings.refresh_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_token(
    token: str,
    settings: Settings,
    *,
    expected_token_type: str = "access",
    require_scopes: bool = True,
    use_refresh_secret: bool = False,
) -> AuthContext:
    decode_kwargs: dict[str, object] = {
        "algorithms": [settings.jwt_algorithm],
        "options": {
            "verify_aud": bool(settings.jwt_audience),
            "verify_iss": bool(settings.jwt_issuer),
        },
    }

    if settings.jwt_audience:
        decode_kwargs["audience"] = settings.jwt_audience

    if settings.jwt_issuer:
        decode_kwargs["issuer"] = settings.jwt_issuer

    secret_key = settings.refresh_secret_key if use_refresh_secret else settings.jwt_secret_key

    try:
        payload = jwt.decode(token, secret_key, **decode_kwargs)
    except ExpiredSignatureError as error:
        raise TokenExpiredError("Token has expired.") from error
    except JWTError as error:
        raise AuthError("Invalid bearer token.") from error

    if not isinstance(payload, dict):
        raise AuthError("Malformed token payload.")

    subject = str(payload.get("sub", "")).strip()
    workspace_id = str(payload.get("workspace_id") or payload.get("workspaceId") or payload.get("workspace") or "").strip()
    token_type = str(payload.get("token_type") or "access").strip().lower()

    if not subject:
        raise AuthError("Token is missing subject.")

    if not workspace_id:
        raise AuthError("Token is missing workspace scope.")

    if token_type != expected_token_type:
        raise AuthError("Token type mismatch.")

    token_scopes = parse_scopes(payload.get("scopes") or payload.get("scope"))
    missing_scopes = settings.required_scopes_set.difference(token_scopes)

    if require_scopes and missing_scopes:
        raise AuthError(
            "Token missing required scope(s): " + ", ".join(sorted(missing_scopes))
        )

    return AuthContext(
        subject=subject,
        workspace_id=workspace_id,
        scopes=token_scopes,
        token_type=token_type,
        token_payload=payload,
    )


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )

    return "$".join(
        [
            _PBKDF2_SCHEME,
            str(_PBKDF2_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations_str, salt_b64, digest_b64 = stored_hash.split("$", 3)
    except ValueError:
        return False

    if scheme != _PBKDF2_SCHEME:
        return False

    try:
        iterations = int(iterations_str)
        salt = base64.urlsafe_b64decode(_pad_base64(salt_b64))
        expected_digest = base64.urlsafe_b64decode(_pad_base64(digest_b64))
    except Exception:
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )

    return hmac.compare_digest(actual_digest, expected_digest)


def extract_websocket_token(websocket: WebSocket) -> str | None:
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            return token

    settings = getattr(websocket.app.state, "settings", None)
    if isinstance(settings, Settings) and settings.ws_allow_subprotocol_token:
        protocol_token = extract_token_from_subprotocol_header(
            websocket.headers.get("sec-websocket-protocol", "")
        )
        if protocol_token:
            return protocol_token

    return None


def extract_token_from_subprotocol_header(raw_subprotocols: str) -> str | None:
    if not raw_subprotocols:
        return None

    protocols = [entry.strip() for entry in raw_subprotocols.split(",") if entry.strip()]
    if not protocols:
        return None

    for index, protocol in enumerate(protocols):
        token = _match_protocol_to_token(protocol, index, protocols)
        if token:
            return token

    return None


def _match_protocol_to_token(protocol: str, index: int, protocols: list[str]) -> str | None:
    normalized = protocol.strip()
    lower = normalized.lower()

    for prefix, offset in (("bearer ", 7), ("bearer.", 7), ("token.", 6), ("jwt.", 4)):
        if lower.startswith(prefix) and len(normalized) > offset:
            return normalized[offset:].strip()

    if lower == "bearer" and index + 1 < len(protocols):
        fallback_token = protocols[index + 1].strip()
        return fallback_token if fallback_token else None

    return None


def require_role(*roles: str):
    """Dependency factory that accepts a list of allowed roles and raises 403 if user role is not in the list.

    Valid roles: "admin", "member", "reviewer", "viewer"
    "admin" implicitly satisfies any role requirement.

    Usage:
        @router.delete("/...", dependencies=[Depends(require_role("admin"))])
        async def handler(auth: AuthContext = Depends(require_role("admin", "reviewer"))): ...
    """
    allowed = set(roles)

    async def _check(
        auth: AuthContext = Depends(get_current_auth_context),
        session: AsyncSession = Depends(get_db_session),
    ) -> AuthContext:
        try:
            user_id = PyUUID(auth.subject)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_TOKEN_SUBJECT)

        from app.db.models import UserAccount  # local import to avoid circular dependency

        result = await session.execute(
            select(UserAccount.role, UserAccount.is_active).where(UserAccount.id == user_id)
        )
        row = result.one_or_none()
        if row is None or not row.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account not found or inactive.",
            )
        if row.role != "admin" and row.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required role(s): {', '.join(sorted(allowed))}. Your role: {row.role}.",
            )
        # Return a new AuthContext carrying the DB-verified role so handlers
        # always see live role state, not a potentially stale JWT claim.
        return dc_replace(auth, token_payload={**auth.token_payload, "role": row.role})

    return _check


# Thin wrapper: require_admin is equivalent to require_role("admin") but
# kept as a named dependency for readability and backward compatibility.
require_admin = require_role("admin")


async def get_verified_user_role(session: AsyncSession, auth: AuthContext) -> str:
    try:
        user_id = PyUUID(auth.subject)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_INVALID_TOKEN_SUBJECT)

    from app.db.models import UserAccount  # local import to avoid circular dependency

    result = await session.execute(
        select(UserAccount.role, UserAccount.is_active).where(UserAccount.id == user_id)
    )
    row = result.one_or_none()
    if row is None or not row.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found or inactive.")
    return row.role


def build_record_visibility_clause(
    record_uuid_column,
    *,
    workspace_id: str,
    user_id: str,
    is_admin: bool,
):
    from app.db.models import ResourcePermission

    if is_admin:
        return literal(True)

    user_permission_exists = exists(
        select(ResourcePermission.id).where(
            ResourcePermission.workspace_id == workspace_id,
            ResourcePermission.record_uuid == cast(record_uuid_column, Text),
            ResourcePermission.user_id == user_id,
            ResourcePermission.can_view.is_(True),
        )
    )
    any_permission_exists = exists(
        select(ResourcePermission.id).where(
            ResourcePermission.workspace_id == workspace_id,
            ResourcePermission.record_uuid == cast(record_uuid_column, Text),
        )
    )
    return or_(user_permission_exists, ~any_permission_exists)


async def require_auth_rate_limit(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI dependency: strict per-IP rate limit for authentication endpoints.

    Applies AUTH_RATE_LIMIT_MAX_REQUESTS / AUTH_RATE_LIMIT_WINDOW_SECONDS (default
    10 req / 60 s) per client IP, independent of the global HTTP rate limit.
    This limits password-guessing attacks without affecting normal API traffic.
    """
    if not settings.rate_limit_enabled:
        return
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    client_ip = get_client_ip(request, settings) or "unknown"
    key = f"auth:{client_ip}"
    decision = await limiter.acheck(
        key=key,
        limit=settings.auth_rate_limit_max_requests,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Please try again later.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


def ensure_workspace_scope(auth: AuthContext, workspace_id: str | None) -> None:
    if workspace_id is not None and workspace_id != auth.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace mismatch: cannot access a different workspace.",
        )


def parse_scopes(raw_scopes: object) -> set[str]:
    if isinstance(raw_scopes, str):
        return {scope.strip() for scope in raw_scopes.split() if scope.strip()}

    if isinstance(raw_scopes, (list, tuple, set)):
        return {str(scope).strip() for scope in raw_scopes if str(scope).strip()}

    return set()


def _pad_base64(value: str) -> str:
    remainder = len(value) % 4
    if remainder == 0:
        return value

    return value + ("=" * (4 - remainder))
