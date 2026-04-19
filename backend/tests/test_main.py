import asyncio
import importlib

from app.core.config import Settings


BASE_SETTINGS = {
    'APP_ENV': 'test',
    'JWT_SECRET_KEY': 'd' * 40,
    'BACKEND_CORS_ORIGINS': 'http://localhost:3000',
}


def build_settings(**overrides: object) -> Settings:
    return Settings.model_validate({**BASE_SETTINGS, **overrides})


def test_initialize_neo4j_service_returns_none_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv('APP_ENV', 'test')
    monkeypatch.setenv('JWT_SECRET_KEY', 'd' * 40)
    monkeypatch.setenv('BACKEND_CORS_ORIGINS', 'http://localhost:3000')

    main_module = importlib.import_module('app.main')
    settings = build_settings(NEO4J_ENABLED=False, BACKEND_MODE='basic')

    assert asyncio.run(main_module.initialize_neo4j_service(settings)) is None


def test_settings_expose_team_product_mode_for_basic_backend() -> None:
    settings = build_settings(NEO4J_ENABLED=False, BACKEND_MODE='basic')

    assert settings.product_mode == 'team'
