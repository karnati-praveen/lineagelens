from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_BACKEND_MODE = "team"
DISALLOWED_JWT_SECRETS = {
    "",
    "change-me",
    "changeme",
    "default",
    "secret",
    "password",
}
DISALLOWED_PROXY_TOKENS = DISALLOWED_JWT_SECRETS | {
    "proxy-token",
    "token",
    "proxy",
    "static-token",
    "proxy-static-token",
}
DISALLOWED_PRODUCTION_PASSWORDS = {
    "",
    "neo4j",
    "neo4j_password",
    "password",
    "postgres",
    "change-me",
    "changeme",
    "default",
    "secret",
    "admin",
}


class Settings(BaseSettings):
    app_env: str = Field(default="development", alias="APP_ENV")
    app_title: str = Field(default="LineageLens", alias="APP_TITLE")
    app_version: str = Field(default="0.1.0", alias="APP_VERSION")

    backend_mode: str = Field(default=DEFAULT_BACKEND_MODE, alias="BACKEND_MODE")
    neo4j_enabled: bool = Field(default=False, alias="NEO4J_ENABLED")
    vector_search_enabled: bool = Field(default=False, alias="VECTOR_SEARCH_ENABLED")
    lineage_strict_mode: bool = Field(default=False, alias="LINEAGE_STRICT_MODE")

    # Defaults to SQLite (lite mode). Override with postgresql+asyncpg://... for Postgres.
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/lineagelens.db",
        alias="DATABASE_URL",
    )
    pgvector_dimension: int = Field(default=256, alias="PGVECTOR_DIMENSION")

    # Connection pool settings (ignored for SQLite)
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
    registration_enabled: bool = Field(default=True, alias="REGISTRATION_ENABLED")

    # Admin auto-seed: if set and no users exist yet, a first admin account is created on startup.
    admin_seed_username: str | None = Field(default=None, alias="ADMIN_SEED_USERNAME")
    admin_seed_password: str | None = Field(default=None, alias="ADMIN_SEED_PASSWORD")
    admin_seed_workspace_id: str | None = Field(default=None, alias="ADMIN_SEED_WORKSPACE_ID")

    backend_cors_origins: str = Field(
        default="http://127.0.0.1:3000,http://localhost:3000",
        alias="BACKEND_CORS_ORIGINS",
    )
    backend_trusted_hosts: str = Field(
        default="",
        alias="BACKEND_TRUSTED_HOSTS",
    )
    trusted_proxy_ips: str = Field(
        default="",
        alias="TRUSTED_PROXY_IPS",
    )

    http_max_body_bytes: int = Field(default=2_000_000, alias="HTTP_MAX_BODY_BYTES")
    ws_max_message_bytes: int = Field(default=2_000_000, alias="WS_MAX_MESSAGE_BYTES")

    redis_url: str | None = Field(default=None, alias="REDIS_URL")

    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS")
    rate_limit_max_requests: int = Field(default=120, alias="RATE_LIMIT_MAX_REQUESTS")
    rate_limit_ws_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WS_WINDOW_SECONDS")
    rate_limit_ws_max_messages: int = Field(default=120, alias="RATE_LIMIT_WS_MAX_MESSAGES")
    rate_limit_ws_max_connections: int = Field(default=30, alias="RATE_LIMIT_WS_MAX_CONNECTIONS")
    rate_limit_max_tracked_keys: int = Field(default=50000, alias="RATE_LIMIT_MAX_TRACKED_KEYS")
    rate_limit_key_prefix: str = Field(default="rl:", alias="RATE_LIMIT_KEY_PREFIX")
    ws_allow_subprotocol_token: bool = Field(default=False, alias="WS_ALLOW_SUBPROTOCOL_TOKEN")

    embedding_provider: str = Field(default="hash", alias="EMBEDDING_PROVIDER")
    embedding_api_url: str = Field(
        default="https://api.openai.com/v1/embeddings", alias="EMBEDDING_API_URL"
    )
    embedding_api_key: str | None = Field(default=None, alias="EMBEDDING_API_KEY")
    embedding_model_name: str = Field(
        default="text-embedding-3-small", alias="EMBEDDING_MODEL_NAME"
    )
    search_default_limit: int = 20

    explain_llm_api_url: str = Field(
        default="https://api.openai.com/v1/chat/completions", alias="EXPLAIN_LLM_API_URL"
    )
    explain_llm_api_key: str | None = Field(default=None, alias="EXPLAIN_LLM_API_KEY")
    explain_llm_model: str = Field(default="gpt-4o-mini", alias="EXPLAIN_LLM_MODEL")
    explain_llm_timeout_seconds: int = Field(default=25, alias="EXPLAIN_LLM_TIMEOUT_SECONDS")

    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, alias="SMTP_USER")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from: str | None = Field(default=None, alias="SMTP_FROM")

    proxy_static_token: str = Field(default="", alias="PROXY_STATIC_TOKEN")

    # Field-level encryption key for sensitive DB columns (GitHub tokens, webhook secrets).
    # If unset the system falls back to deriving a key from JWT_SECRET_KEY.
    # Set explicitly in production to decouple field encryption from JWT rotation.
    field_encryption_key: str | None = Field(default=None, alias="FIELD_ENCRYPTION_KEY")

    # Ed25519 attestation signing key: base64-encoded 32-byte raw seed.
    # If unset in non-production a key is derived from JWT_SECRET_KEY (logged as warning).
    # Must be set explicitly in production (enforced by validate_secrets_for_environment).
    attestation_signing_key: str | None = Field(default=None, alias="ATTESTATION_SIGNING_KEY")

    # Path to a JSON file containing license fingerprint corpus for F5 license matching.
    # If unset, all scans return "clean" (safe default — no false positives without corpus).
    license_fingerprint_path: str | None = Field(default=None, alias="LICENSE_FINGERPRINT_PATH")

    # When true, records with unknown human-review status pass the F1 eligibility check.
    # When false (default), unknown review status causes ineligibility for indemnity.
    indemnity_unknown_review_pass: bool = Field(default=False, alias="INDEMNITY_UNKNOWN_REVIEW_PASS")

    # Tighter per-IP rate limit applied exclusively to authentication endpoints.
    # This is separate from the global HTTP rate limit so that login brute-force
    # attempts are throttled independently of normal API traffic.
    auth_rate_limit_max_requests: int = Field(default=10, alias="AUTH_RATE_LIMIT_MAX_REQUESTS")
    auth_rate_limit_window_seconds: int = Field(default=60, alias="AUTH_RATE_LIMIT_WINDOW_SECONDS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origins(self) -> list[str]:
        origins = [entry.strip() for entry in self.backend_cors_origins.split(",") if entry.strip()]
        return list(dict.fromkeys(origins))

    @property
    def trusted_hosts(self) -> list[str]:
        hosts = [entry.strip() for entry in self.backend_trusted_hosts.split(",") if entry.strip()]
        return list(dict.fromkeys(hosts))

    @property
    def required_scopes_set(self) -> set[str]:
        return {scope.strip() for scope in self.jwt_required_scopes.split() if scope.strip()}

    @property
    def refresh_secret_key(self) -> str:
        return self.jwt_refresh_secret_key or self.jwt_secret_key

    @property
    def is_solo_mode(self) -> bool:
        return self.backend_mode == "solo"

    @property
    def product_mode(self) -> str:
        return {"solo": "lite", "enterprise": "max"}.get(self.backend_mode, "plus")

    @field_validator("proxy_static_token")
    @classmethod
    def validate_proxy_static_token(cls, value: str) -> str:
        token = value.strip()
        if not token:
            return value
        if token.lower() in DISALLOWED_PROXY_TOKENS:
            raise ValueError(
                "PROXY_STATIC_TOKEN must not be set to a known-weak value."
            )
        if len(token) < 32:
            raise ValueError(
                "PROXY_STATIC_TOKEN must be at least 32 characters long when set."
            )
        return value

    @field_validator("auth_password_min_length")
    @classmethod
    def validate_auth_password_min_length(cls, value: int) -> int:
        if value < 8:
            raise ValueError("AUTH_PASSWORD_MIN_LENGTH must be at least 8.")
        return value

    @field_validator("pgvector_dimension")
    @classmethod
    def validate_pgvector_dimension(cls, value: int) -> int:
        if value != 256:
            raise ValueError("PGVECTOR_DIMENSION must be 256 to match the database schema.")
        return value

    @model_validator(mode="after")
    def validate_vector_search_for_database(self) -> "Settings":
        if self.is_sqlite and self.vector_search_enabled:
            raise ValueError(
                "VECTOR_SEARCH_ENABLED requires PostgreSQL with pgvector; disable it for SQLite/Lite mode."
            )
        return self

    @field_validator("backend_mode")
    @classmethod
    def validate_backend_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        aliases = {"basic": "team", "full": "enterprise"}
        mode = aliases.get(mode, mode)
        if mode not in {"solo", "team", "enterprise"}:
            raise ValueError("BACKEND_MODE must be 'solo', 'team', or 'enterprise'.")
        return mode

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, value: str) -> str:
        secret = value.strip()
        if secret.lower() in DISALLOWED_JWT_SECRETS:
            raise ValueError("JWT_SECRET_KEY must be set to a strong, non-default value.")
        if len(secret) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters long.")
        return secret

    @field_validator("http_max_body_bytes", "ws_max_message_bytes")
    @classmethod
    def validate_payload_limits(cls, value: int) -> int:
        if value < 8_192:
            raise ValueError("Payload limit values must be at least 8192 bytes.")
        return value

    @model_validator(mode="after")
    def validate_cors_for_environment(self) -> "Settings":
        if "*" in self.cors_origins and len(self.cors_origins) > 1:
            raise ValueError("BACKEND_CORS_ORIGINS wildcard '*' must not be combined with explicit origins.")
        if self.app_env.strip().lower() == "production" and not self.cors_origins:
            raise ValueError("BACKEND_CORS_ORIGINS must not be empty in production.")
        if self.app_env.strip().lower() == "production" and any(
            origin == "*" for origin in self.cors_origins
        ):
            raise ValueError("BACKEND_CORS_ORIGINS must not contain wildcard '*' in production.")
        return self

    @model_validator(mode="after")
    def validate_secrets_for_environment(self) -> "Settings":
        if self.app_env.strip().lower() != "production":
            return self

        if self.neo4j_enabled and self.neo4j_password.strip().lower() in DISALLOWED_PRODUCTION_PASSWORDS:
            raise ValueError("NEO4J_PASSWORD must be set to a strong, non-default value in production.")

        if self.jwt_refresh_secret_key is None or self.jwt_refresh_secret_key.strip() == "":
            raise ValueError("JWT_REFRESH_SECRET_KEY must be set explicitly in production.")

        refresh_secret = self.jwt_refresh_secret_key.strip()
        if refresh_secret.lower() in DISALLOWED_JWT_SECRETS:
            raise ValueError("JWT_REFRESH_SECRET_KEY must be set to a strong, non-default value.")
        if len(refresh_secret) < 32:
            raise ValueError("JWT_REFRESH_SECRET_KEY must be at least 32 characters long.")

        if self.jwt_refresh_secret_key == self.jwt_secret_key:
            raise ValueError(
                "JWT_REFRESH_SECRET_KEY must differ from JWT_SECRET_KEY in production."
            )

        if not self.attestation_signing_key or not self.attestation_signing_key.strip():
            raise ValueError(
                "ATTESTATION_SIGNING_KEY must be set explicitly in production. "
                "Generate with: python -c \"import base64,os; print(base64.b64encode(os.urandom(32)).decode())\""
            )
        try:
            import base64 as _b64
            seed = _b64.b64decode(self.attestation_signing_key.strip())
            if len(seed) != 32:
                raise ValueError(f"Expected 32 bytes after base64 decode, got {len(seed)}")
        except Exception as exc:
            raise ValueError(f"ATTESTATION_SIGNING_KEY is not a valid base64-encoded 32-byte value: {exc}") from exc

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
