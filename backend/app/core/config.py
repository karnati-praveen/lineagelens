from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_PGVECTOR_DIMENSION = 256
DISALLOWED_JWT_SECRETS = {
    "",
    "change-me",
    "changeme",
    "default",
    "secret",
    "password",
}


class Settings(BaseSettings):
    app_env: str = Field(default="development", alias="APP_ENV")
    app_title: str = Field(default="AI Provenance Backend", alias="APP_TITLE")
    app_version: str = Field(default="0.1.0", alias="APP_VERSION")

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/provenance",
        alias="DATABASE_URL",
    )
    pgvector_dimension: int = Field(default=DEFAULT_PGVECTOR_DIMENSION, alias="PGVECTOR_DIMENSION")

    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout_seconds: int = Field(default=30, alias="DB_POOL_TIMEOUT_SECONDS")
    db_pool_recycle_seconds: int = Field(default=1800, alias="DB_POOL_RECYCLE_SECONDS")

    neo4j_uri: str = Field(default="bolt://127.0.0.1:7687", alias="NEO4J_URI")
    neo4j_username: str = Field(default="neo4j", alias="NEO4J_USERNAME")
    neo4j_password: str = Field(default="neo4j_password", alias="NEO4J_PASSWORD")
    neo4j_database: str = Field(default="neo4j", alias="NEO4J_DATABASE")

    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY")
    jwt_refresh_secret_key: str | None = Field(default=None, alias="JWT_REFRESH_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_audience: str | None = Field(default="provenance-api", alias="JWT_AUDIENCE")
    jwt_issuer: str | None = Field(default="provenance-backend", alias="JWT_ISSUER")
    jwt_access_token_ttl_minutes: int = Field(default=30, alias="JWT_ACCESS_TOKEN_TTL_MINUTES")
    jwt_refresh_token_ttl_minutes: int = Field(default=10080, alias="JWT_REFRESH_TOKEN_TTL_MINUTES")
    jwt_required_scopes: str = Field(
        default="provenance:write provenance:read", alias="JWT_REQUIRED_SCOPES"
    )
    auth_password_min_length: int = Field(default=8, alias="AUTH_PASSWORD_MIN_LENGTH")

    backend_cors_origins: str = Field(
        default="http://127.0.0.1:3000,http://localhost:3000",
        alias="BACKEND_CORS_ORIGINS",
    )

    http_max_body_bytes: int = Field(default=2_000_000, alias="HTTP_MAX_BODY_BYTES")
    ws_max_message_bytes: int = Field(default=2_000_000, alias="WS_MAX_MESSAGE_BYTES")

    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS")
    rate_limit_max_requests: int = Field(default=120, alias="RATE_LIMIT_MAX_REQUESTS")
    rate_limit_ws_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WS_WINDOW_SECONDS")
    rate_limit_ws_max_messages: int = Field(default=120, alias="RATE_LIMIT_WS_MAX_MESSAGES")
    rate_limit_ws_max_connections: int = Field(default=30, alias="RATE_LIMIT_WS_MAX_CONNECTIONS")
    rate_limit_max_tracked_keys: int = Field(default=50000, alias="RATE_LIMIT_MAX_TRACKED_KEYS")

    embedding_model_name: str = "deterministic-hash-v1"
    search_default_limit: int = 20

    explain_llm_api_url: str = Field(
        default="https://api.openai.com/v1/chat/completions", alias="EXPLAIN_LLM_API_URL"
    )
    explain_llm_api_key: str | None = Field(default=None, alias="EXPLAIN_LLM_API_KEY")
    explain_llm_model: str = Field(default="gpt-4o-mini", alias="EXPLAIN_LLM_MODEL")
    explain_llm_timeout_seconds: int = Field(default=25, alias="EXPLAIN_LLM_TIMEOUT_SECONDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        origins = [entry.strip() for entry in self.backend_cors_origins.split(",") if entry.strip()]
        return list(dict.fromkeys(origins))

    @property
    def required_scopes_set(self) -> set[str]:
        return {scope.strip() for scope in self.jwt_required_scopes.split() if scope.strip()}

    @property
    def refresh_secret_key(self) -> str:
        return self.jwt_refresh_secret_key or self.jwt_secret_key

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, value: str) -> str:
        secret = value.strip()
        if secret.lower() in DISALLOWED_JWT_SECRETS:
            raise ValueError("JWT_SECRET_KEY must be set to a strong, non-default value.")

        if len(secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters long.")

        return secret

    @field_validator("pgvector_dimension")
    @classmethod
    def validate_pgvector_dimension(cls, value: int) -> int:
        if value != DEFAULT_PGVECTOR_DIMENSION:
            raise ValueError(
                f"PGVECTOR_DIMENSION must be {DEFAULT_PGVECTOR_DIMENSION} to match the database schema."
            )
        return value

    @field_validator("http_max_body_bytes", "ws_max_message_bytes")
    @classmethod
    def validate_payload_limits(cls, value: int) -> int:
        if value < 8_192:
            raise ValueError("Payload limit values must be at least 8192 bytes.")
        return value

    @model_validator(mode="after")
    def validate_cors_for_environment(self) -> "Settings":
        if self.app_env.strip().lower() == "production" and not self.cors_origins:
            raise ValueError("BACKEND_CORS_ORIGINS must not be empty in production.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
