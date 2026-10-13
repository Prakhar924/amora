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

    # Host header allow-list. "*" is fine behind a proxy that already pins the
    # host, but in production it enables Host-header poisoning of the reset and
    # verification links we email out, so it is rejected there.
    allowed_hosts: str = "*"

    # Serve the interactive OpenAPI docs. Off in production by default: the
    # schema is a map of the attack surface and there is no reason to publish
    # it alongside a consumer app.
    enable_docs: bool = True

    # Number of uvicorn workers. Only read by the process manager, not the app.
    web_concurrency: int = 2

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
    # When true, a failed send is logged instead of raised. Indispensable in
    # development (no SMTP server), dangerous in production: a user would be
    # told to check an inbox that will never receive anything.
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

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def assert_production_ready(self) -> None:
        """Fail fast rather than serve production traffic with an unsafe config.

        This is the last line of defence against a misconfigured deploy. Each
        check below corresponds to a way a real deployment silently becomes
        insecure: a default secret lets anyone mint tokens, a localhost CORS
        origin means the real frontend was never configured, a wildcard host
        allows Host-header poisoning of emailed reset links, and a console
        email fallback means password resets vanish into a log file.
        """
        if self.app_env != "production":
            return

        problems: list[str] = []

        if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
            problems.append(
                "SECRET_KEY must be unique and at least 32 characters. "
                'Generate one with: python -c "import secrets; '
                "print(secrets.token_urlsafe(48))\""
            )
        if self.postgres_password in {"amora", "postgres", "password", ""}:
            problems.append("POSTGRES_PASSWORD must not be a default value")
        if self.s3_secret_key in {"amoraminio", "amoralocal", "test", ""}:
            problems.append("S3_SECRET_KEY must be a real object-storage credential")
        if self.debug:
            problems.append("DEBUG must be false in production")

        # Transport security: tokens and photos must never cross plain HTTP.
        insecure_origins = [
            o
            for o in self.cors_origin_list
            if o.startswith("http://") and "localhost" not in o and "127.0.0.1" not in o
        ]
        if insecure_origins:
            problems.append(
                "CORS_ORIGINS must use https:// in production; got "
                + ", ".join(insecure_origins)
            )
        if not self.cors_origin_list:
            problems.append("CORS_ORIGINS must list the frontend origin(s)")
        if any("localhost" in o for o in self.cors_origin_list):
            problems.append(
                "CORS_ORIGINS still contains localhost — set it to the real "
                "frontend origin"
            )

        if self.allowed_hosts.strip() == "*":
            problems.append(
                "ALLOWED_HOSTS must name the API's real hostname(s), not '*'. "
                "A wildcard allows Host-header poisoning of emailed links."
            )

        if self.s3_public_endpoint_url and self.s3_public_endpoint_url.startswith(
            "http://"
        ):
            problems.append("S3_PUBLIC_ENDPOINT_URL must be https:// in production")
        if self.frontend_base_url.startswith("http://"):
            problems.append("FRONTEND_BASE_URL must be https:// in production")

        # Email: a silent fallback turns "reset your password" into a dead end.
        if self.email_console_fallback:
            problems.append(
                "EMAIL_CONSOLE_FALLBACK must be false in production, or failed "
                "verification and password-reset emails will be silently logged"
            )
        if self.smtp_host in {"localhost", "mailpit", "mailhog", ""}:
            problems.append("SMTP_HOST must point at a real mail provider")

        if self.bcrypt_rounds < 10:
            problems.append("BCRYPT_ROUNDS must be at least 10 in production")

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
