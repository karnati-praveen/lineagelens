import pytest

from app.core.config import Settings


BASE_SETTINGS = {
    "APP_ENV": "test",
    "JWT_SECRET_KEY": "a" * 40,
    "BACKEND_CORS_ORIGINS": "http://localhost:3000,http://localhost:3000",
}


def build_settings(**overrides: object) -> Settings:
    payload = {**BASE_SETTINGS, **overrides}
    return Settings.model_validate(payload)


def test_settings_accepts_strong_jwt_secret() -> None:
    settings = build_settings()

    assert settings.jwt_secret_key == "a" * 40
    assert settings.pgvector_dimension == 256
    assert settings.backend_mode == "team"
    assert settings.product_mode == "plus"
    assert not settings.neo4j_enabled
    assert not settings.vector_search_enabled
    assert not settings.lineage_strict_mode


def test_settings_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValueError, match="at least 32 characters"):
        build_settings(JWT_SECRET_KEY="too-short-secret")


def test_settings_rejects_empty_prod_cors_origins() -> None:
    with pytest.raises(ValueError, match="BACKEND_CORS_ORIGINS must not be empty"):
        build_settings(APP_ENV="production", BACKEND_CORS_ORIGINS="")


def test_settings_deduplicates_cors_origins() -> None:
    settings = build_settings(BACKEND_CORS_ORIGINS="http://localhost:3000,http://localhost:3000")

    assert settings.cors_origins == ["http://localhost:3000"]


def test_settings_rejects_wildcard_mixed_with_explicit_cors_origins() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        build_settings(BACKEND_CORS_ORIGINS="*,http://localhost:3000")


def test_settings_rejects_vector_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="PGVECTOR_DIMENSION must be 256"):
        build_settings(PGVECTOR_DIMENSION=384)


def test_settings_rejects_vector_search_on_sqlite() -> None:
    # Explicitly force SQLite so the DATABASE_URL env var from the Postgres CI
    # job does not override the default and suppress the expected ValueError.
    with pytest.raises(ValueError, match="VECTOR_SEARCH_ENABLED requires PostgreSQL"):
        build_settings(
            VECTOR_SEARCH_ENABLED=True,
            DATABASE_URL="sqlite+aiosqlite:///./data/test.db",
        )


def test_settings_rejects_invalid_backend_mode() -> None:
    with pytest.raises(ValueError, match="BACKEND_MODE must be 'solo', 'team', or 'enterprise'"):
        build_settings(BACKEND_MODE="unsupported")


def test_settings_maps_full_backend_to_enterprise_product_mode() -> None:
    settings = build_settings(BACKEND_MODE="full")

    assert settings.product_mode == "max"


def test_settings_registration_enabled_by_default() -> None:
    settings = build_settings()

    assert settings.registration_enabled is True


def test_settings_registration_can_be_disabled() -> None:
    settings = build_settings(REGISTRATION_ENABLED=False)

    assert settings.registration_enabled is False


# ── M3: PROXY_STATIC_TOKEN validation ─────────────────────────────────────────

def test_settings_accepts_empty_proxy_static_token() -> None:
    settings = build_settings(PROXY_STATIC_TOKEN="")
    assert settings.proxy_static_token == ""


def test_settings_accepts_strong_proxy_static_token() -> None:
    strong = "x" * 40
    settings = build_settings(PROXY_STATIC_TOKEN=strong)
    assert settings.proxy_static_token == strong


def test_settings_rejects_short_proxy_static_token() -> None:
    with pytest.raises(ValueError, match="at least 32 characters"):
        build_settings(PROXY_STATIC_TOKEN="short-token")


def test_settings_rejects_weak_proxy_static_token() -> None:
    with pytest.raises(ValueError, match="known-weak"):
        build_settings(PROXY_STATIC_TOKEN="proxy-token")


def test_settings_rejects_secret_as_proxy_static_token() -> None:
    with pytest.raises(ValueError, match="known-weak"):
        build_settings(PROXY_STATIC_TOKEN="secret")


def test_settings_rejects_token_exactly_31_chars() -> None:
    with pytest.raises(ValueError, match="at least 32 characters"):
        build_settings(PROXY_STATIC_TOKEN="x" * 31)


def test_settings_accepts_token_exactly_32_chars() -> None:
    settings = build_settings(PROXY_STATIC_TOKEN="x" * 32)
    assert len(settings.proxy_static_token) == 32


# ── W1: single source of truth for SMTP + VECTOR_SEARCH_ENABLED ───────────────

def test_settings_exposes_smtp_fields_with_defaults() -> None:
    settings = build_settings()
    assert settings.smtp_host is None
    assert settings.smtp_port == 587
    assert settings.smtp_user is None
    assert settings.smtp_password is None
    assert settings.smtp_from is None


def test_settings_reads_smtp_host() -> None:
    settings = build_settings(SMTP_HOST="smtp.example.com", SMTP_FROM="alerts@example.com")
    assert settings.smtp_host == "smtp.example.com"
    assert settings.smtp_from == "alerts@example.com"


def test_vector_search_enabled_consistent_with_models() -> None:
    """Settings.vector_search_enabled is the single reader; models._vector_search_active
    now delegates to it, so both sides agree on the same value."""
    from app.core.config import get_settings
    from app.db import models as db_models

    cfg = get_settings()
    # _vector_search_active is False in the test env (VECTOR_SEARCH_ENABLED=false in conftest).
    # Verify Settings agrees so the two paths can never diverge.
    assert cfg.vector_search_enabled == db_models._vector_search_active
