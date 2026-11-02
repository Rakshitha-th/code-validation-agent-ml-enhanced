from pydantic_settings import BaseSettings
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    service_name: str = "gateway"
    host: str = "0.0.0.0"
    port: int = 8000
    orchestrator_url: str = "http://orchestrator:8000"
    quality_service_url: str = "http://code-quality:8000"
    security_service_url: str = "http://security:8000"
    repair_service_url: str = "http://repair:8000"
    sandbox_service_url: str = "http://sandbox:8004"
    validation_service_url: str = "http://validation:8000"
    bug_resource_service_url: str | None = "http://bug-resource:8000"
    report_recommendation_service_url: str = "http://report-recommendation:8000"
    database_url: str = "sqlite+aiosqlite:///./gateway.db"
    database_host: str | None = None
    database_port: int = 5432
    database_name: str | None = None
    database_user: str | None = None
    database_password: str | None = None
    request_timeout_seconds: float = 120.0
    cors_origins: str = "http://localhost:5174,http://127.0.0.1:5174,http://localhost:5173,http://127.0.0.1:5173"
    auth_cookie_secure: bool = False
    local_demo_auth_enabled: bool = False
    auth_session_hours: int = 12
    auth_remember_days: int = 30
    auth_reset_token_minutes: int = 30
    frontend_public_url: str = "http://localhost:5174"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "System Administrator"

    class Config:
        env_prefix = "GATEWAY_"

    @property
    def resolved_database_url(self) -> str | URL:
        if self.database_host is None:
            return self.database_url
        if not self.database_name or not self.database_user or self.database_password is None:
            raise ValueError("PostgreSQL database name, user, and password are required when a host is configured.")
        return URL.create(
            "postgresql+asyncpg",
            username=self.database_user,
            password=self.database_password,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )

    @property
    def migration_database_url(self) -> str:
        database_url = self.resolved_database_url
        if isinstance(database_url, URL):
            return database_url.render_as_string(hide_password=False)
        return database_url


settings = Settings()