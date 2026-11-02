from sqlalchemy.engine import make_url

from app.config import Settings


def test_migration_database_url_uses_resolved_postgresql_settings():
    settings = Settings(
        _env_file=None,
        database_host="postgres",
        database_port=5432,
        database_name="validation",
        database_user="agent",
        database_password="pass@word/with?symbols",
    )

    url = make_url(settings.migration_database_url)

    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "postgres"
    assert url.database == "validation"
    assert url.username == "agent"
    assert url.password == "pass@word/with?symbols"


def test_migration_database_url_preserves_explicit_url_without_postgres_host():
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///./gateway.db",
    )

    assert settings.migration_database_url == "sqlite+aiosqlite:///./gateway.db"
