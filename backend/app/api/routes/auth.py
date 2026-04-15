import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import (
    AuthContext,
    AuthError,
    TokenExpiredError,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_auth_context,
    hash_password,
    verify_password,
)
from app.db.models import UserAccount
from app.db.session import get_db_session
from app.schemas.auth import (
    AuthTokenResponse,
    AuthUserResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AuthTokenResponse:
    username = normalize_username(payload.username)
    validate_password_strength(payload.password, settings)

    existing_user = await get_user_by_username(session, username)
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username is already registered.",
        )

    workspace_id = normalize_workspace_id(payload.workspace_id) or create_default_workspace_id(username)

    user = UserAccount(
        username=username,
        password_hash=hash_password(payload.password),
        workspace_id=workspace_id,
        is_active=True,
    )

    session.add(user)
    await session.commit()
    await session.refresh(user)

    return issue_token_response(user, settings)


@router.post("/login", response_model=AuthTokenResponse)
async def login_user(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AuthTokenResponse:
    username = normalize_username(payload.username)
    user = await get_user_by_username(session, username)

    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    requested_workspace = normalize_workspace_id(payload.workspace_id)
    if requested_workspace and requested_workspace != user.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace scope mismatch.",
        )

    return issue_token_response(user, settings)


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh_access_token(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> AuthTokenResponse:
    raw_refresh_token = payload.refresh_token.strip()
    if not raw_refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refresh token is required.",
        )

    try:
        refresh_auth = decode_token(
            raw_refresh_token,
            settings,
            expected_token_type="refresh",
            require_scopes=False,
            use_refresh_secret=True,
        )
    except TokenExpiredError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error
    except AuthError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    user = await get_user_by_id(session, refresh_auth.subject)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token user.",
        )

    if user.workspace_id != refresh_auth.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token workspace mismatch.",
        )

    return issue_token_response(user, settings)


@router.get("/me")
async def get_authenticated_user(
    auth: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    user = await get_user_by_id(session, auth.subject)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Authenticated user not found.",
        )

    return {
        "id": str(user.id),
        "username": user.username,
        "workspaceId": user.workspace_id,
        "scopes": sorted(auth.scopes),
    }


def issue_token_response(user: UserAccount, settings: Settings) -> AuthTokenResponse:
    scopes = sorted(settings.required_scopes_set)

    access_token, access_expires_at = create_access_token(
        subject=str(user.id),
        workspace_id=user.workspace_id,
        scopes=scopes,
        settings=settings,
        extra_claims={"username": user.username},
    )

    refresh_token, _ = create_refresh_token(
        subject=str(user.id),
        workspace_id=user.workspace_id,
        settings=settings,
        extra_claims={"username": user.username},
    )

    now_utc = datetime.now(tz=UTC)
    expires_in_seconds = max(1, int((access_expires_at - now_utc).total_seconds()))

    return AuthTokenResponse(
        accessToken=access_token,
        refreshToken=refresh_token,
        tokenType="bearer",
        expiresInSeconds=expires_in_seconds,
        expiresAtIso=access_expires_at.isoformat(),
        workspaceId=user.workspace_id,
        user=AuthUserResponse(
            id=str(user.id),
            username=user.username,
            workspaceId=user.workspace_id,
        ),
    )


async def get_user_by_username(session: AsyncSession, username: str) -> UserAccount | None:
    statement = select(UserAccount).where(UserAccount.username == username)
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: str) -> UserAccount | None:
    try:
        parsed_user_id = uuid.UUID(user_id)
    except (ValueError, TypeError):
        return None

    statement = select(UserAccount).where(UserAccount.id == parsed_user_id)
    result = await session.execute(statement)
    return result.scalar_one_or_none()


def normalize_username(raw_username: str) -> str:
    username = (raw_username or "").strip().lower()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is required.",
        )

    if len(username) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must be at most 128 characters.",
        )

    if not re.fullmatch(r"[a-z0-9._@-]+", username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username contains invalid characters.",
        )

    return username


def normalize_workspace_id(raw_workspace_id: str | None) -> str | None:
    if raw_workspace_id is None:
        return None

    workspace_id = raw_workspace_id.strip()
    if not workspace_id:
        return None

    if len(workspace_id) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID must be at most 128 characters.",
        )

    if not re.fullmatch(r"[A-Za-z0-9._:-]+", workspace_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace ID contains invalid characters.",
        )

    return workspace_id


def create_default_workspace_id(username: str) -> str:
    compact_name = re.sub(r"[^a-z0-9]+", "-", username.lower()).strip("-")
    if not compact_name:
        compact_name = "workspace"

    return f"ws-{compact_name}-{uuid.uuid4().hex[:8]}"


def validate_password_strength(password: str, settings: Settings) -> None:
    minimum_length = max(8, settings.auth_password_min_length)

    if len(password) < minimum_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at least {minimum_length} characters long.",
        )
