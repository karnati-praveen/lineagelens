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
    assert settings.backend_mode == "basic"
    assert settings.product_mode == "team"
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


def test_settings_rejects_vector_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="PGVECTOR_DIMENSION must be 256"):
        build_settings(PGVECTOR_DIMENSION=384)


def test_settings_rejects_invalid_backend_mode() -> None:
    with pytest.raises(ValueError, match="BACKEND_MODE must be either 'basic' or 'full'"):
        build_settings(BACKEND_MODE="unsupported")


def test_settings_maps_full_backend_to_enterprise_product_mode() -> None:
    settings = build_settings(BACKEND_MODE="full")

    assert settings.product_mode == "enterprise"
