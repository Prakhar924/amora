"""Environment-driven application configuration.

Every secret is read from the environment. The defaults here are only ever
suitable for local development against the bundled docker-compose stack, and
`Settings.assert_production_ready()` refuses to let an obviously-insecure
configuration boot with APP_ENV=production.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- General ---------------------------------------------------------
    app_name: str = "Amora"
    app_env: Literal["development", "test", "production"] = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"

    # Comma-separated list of origins allowed to call the API from a browser.
    cors_origins: str = "http://localhost:8081,http://localhost:19006,http://localhost:3000"

    # --- Database --------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "amora"
    postgres_password: str = "amora"
    postgres_db: str = "amora"

    # --- Redis -----------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- Auth ------------------------------------------------------------
    secret_key: str = DEV_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    # Cost factor for bcrypt. Lowered in tests so the suite stays fast.
    bcrypt_rounds: int = 12

    # --- Object storage (S3 compatible: MinIO locally, S3/R2 in prod) ----
    s3_endpoint_url: str | None = "http://localhost:9000"
    # Endpoint handed to clients for presigned URLs. Differs from
    # `s3_endpoint_url` under docker-compose, where the API talks to
    # "http://minio:9000" but a phone must use the host's address.
    s3_public_endpoint_url: str | None = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "amoraminio"
    s3_secret_key: str = "amoraminio"
    s3_bucket: str = "amora-photos"
    s3_presign_ttl_seconds: int = 900

    # --- Email -----------------------------------------------------------
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "no-reply@amora.dev"
    smtp_use_tls: bool = False
    # When true, verification/reset emails are logged instead of sent.
    email_console_fallback: bool = True

    # --- Product rules ---------------------------------------------------
    minimum_age: int = 18
    maximum_age: int = 120
    max_photos_per_user: int = 6
    max_photo_bytes: int = 8 * 1024 * 1024
    discovery_page_size: int = 20
    # Photos require admin approval before they become publicly visible.
    require_photo_moderation: bool = True

    # --- Rate limits (requests / window seconds) -------------------------
    rate_limit_login: str = "10/300"
    rate_limit_register: str = "5/3600"
    rate_limit_swipe: str = "500/3600"
    rate_limit_message: str = "120/60"
    rate_limit_report: str = "20/86400"
    rate_limit_password_reset: str = "5/3600"

    frontend_base_url: str = "http://localhost:8081"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Used by Alembic, which we run with a sync driver."""
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def assert_production_ready(self) -> None:
        """Fail fast rather than serve production traffic with dev secrets."""
        if self.app_env != "production":
            return
        problems: list[str] = []
        if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be set to a unique value of >=32 chars")
        if self.postgres_password in {"amora", "postgres", ""}:
            problems.append("POSTGRES_PASSWORD must not be a default value")
        if self.s3_secret_key == "amoraminio":
            problems.append("S3_SECRET_KEY must not be a default value")
        if self.debug:
            problems.append("DEBUG must be false in production")
        if problems:
            raise RuntimeError(
                "Refusing to start in production with an insecure configuration:\n  - "
                + "\n  - ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.assert_production_ready()
    return settings


settings = get_settings()
