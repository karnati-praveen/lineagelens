from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegisterRequest(BaseModel):
    username: str
    password: str
    workspace_id: str | None = Field(default=None, alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class LoginRequest(BaseModel):
    username: str
    password: str
    workspace_id: str | None = Field(default=None, alias="workspaceId")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RefreshRequest(BaseModel):
    refresh_token: str = Field(alias="refreshToken")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class AuthUserResponse(BaseModel):
    id: str
    username: str
    workspace_id: str = Field(alias="workspaceId")
    role: str

    model_config = ConfigDict(populate_by_name=True, by_alias=True)


class LogoutResponse(BaseModel):
    logged_out: bool = Field(default=True, alias="loggedOut")

    model_config = ConfigDict(populate_by_name=True, by_alias=True)


class AuthTokenResponse(BaseModel):
    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    token_type: str = Field(default="bearer", alias="tokenType")
    expires_in_seconds: int = Field(alias="expiresInSeconds")
    expires_at_iso: str = Field(alias="expiresAtIso")
    workspace_id: str = Field(alias="workspaceId")
    user: AuthUserResponse

    model_config = ConfigDict(populate_by_name=True, by_alias=True)


_VALID_ROLES = {"admin", "member", "reviewer", "viewer"}


class CreateInviteRequest(BaseModel):
    workspace_id: str = Field(alias="workspaceId")
    role: str = Field(default="member")
    ttl_minutes: int = Field(default=1440, alias="ttlMinutes", ge=1, le=20160)
    max_uses: int = Field(default=1, alias="maxUses", ge=1, le=100)

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}")
        return v


class CreateInviteResponse(BaseModel):
    token: str
    workspace_id: str = Field(alias="workspaceId")
    role: str
    max_uses: int = Field(alias="maxUses")
    expires_at: str = Field(alias="expiresAt")

    model_config = ConfigDict(populate_by_name=True, by_alias=True)


class AcceptInviteRequest(BaseModel):
    token: str
    username: str
    password: str

    model_config = ConfigDict(extra="forbid")
