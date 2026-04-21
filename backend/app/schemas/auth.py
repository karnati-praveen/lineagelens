from pydantic import BaseModel, ConfigDict, Field


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

    model_config = ConfigDict(populate_by_name=True)


class LogoutResponse(BaseModel):
    logged_out: bool = Field(default=True, alias="loggedOut")

    model_config = ConfigDict(populate_by_name=True)


class AuthTokenResponse(BaseModel):
    access_token: str = Field(alias="accessToken")
    refresh_token: str = Field(alias="refreshToken")
    token_type: str = Field(default="bearer", alias="tokenType")
    expires_in_seconds: int = Field(alias="expiresInSeconds")
    expires_at_iso: str = Field(alias="expiresAtIso")
    workspace_id: str = Field(alias="workspaceId")
    user: AuthUserResponse

    model_config = ConfigDict(populate_by_name=True)
