#!/usr/bin/env python3
"""Validate a production .env file before deploying it.

Runs the application's own `Settings.assert_production_ready()` against the
file, plus deployment-level checks the app cannot make (unreplaced
placeholders, a CORS origin that is an origin rather than a URL, and so on).

    python deploy/check-config.py .env.production

Exits non-zero on any problem, so it can gate a deploy script.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLACEHOLDERS = (
    "REPLACE_ME",
    "ACCOUNT_ID",
    "example.com",
    "dev-only-insecure-secret-change-me",
)

REQUIRED = (
    "API_DOMAIN",
    "ACME_EMAIL",
    "SECRET_KEY",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "REDIS_PASSWORD",
    "S3_ENDPOINT_URL",
    "S3_PUBLIC_ENDPOINT_URL",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "S3_BUCKET",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "FRONTEND_BASE_URL",
    "CORS_ORIGINS",
)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path-to-env-file>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2

    env = parse_env(path)
    problems: list[str] = []
    warnings: list[str] = []

    # --- Deployment-level checks -------------------------------------------
    for key in REQUIRED:
        if not env.get(key):
            problems.append(f"{key} is missing or empty")

    for key, value in env.items():
        for placeholder in PLACEHOLDERS:
            if placeholder in value:
                problems.append(f"{key} still contains the placeholder {placeholder!r}")

    # A CORS entry must be a bare origin. "https://user.github.io/amora" never
    # matches a browser's Origin header, and the resulting failure looks like
    # a server error rather than a config mistake.
    for origin in env.get("CORS_ORIGINS", "").split(","):
        origin = origin.strip()
        if not origin:
            continue
        if not re.fullmatch(r"https?://[^/]+", origin):
            problems.append(
                f"CORS_ORIGINS entry {origin!r} must be a bare origin with no "
                "path or trailing slash (e.g. https://user.github.io)"
            )

    if env.get("API_DOMAIN", "").startswith(("http://", "https://")):
        problems.append("API_DOMAIN must be a bare hostname, not a URL")

    if env.get("FRONTEND_BASE_URL", "").endswith("/"):
        warnings.append("FRONTEND_BASE_URL has a trailing slash; links may double up")

    secret = env.get("SECRET_KEY", "")
    if secret and len(secret) < 32:
        problems.append("SECRET_KEY is shorter than 32 characters")

    for key in ("POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        value = env.get(key, "")
        if value and len(value) < 16:
            warnings.append(f"{key} is short ({len(value)} chars); 32 is recommended")

    if env.get("ENABLE_DOCS", "false").lower() == "true":
        warnings.append(
            "ENABLE_DOCS=true publishes the OpenAPI schema; fine for a soft "
            "launch, worth turning off afterwards"
        )
    if env.get("REQUIRE_PHOTO_MODERATION", "true").lower() != "true":
        warnings.append(
            "REQUIRE_PHOTO_MODERATION is off — photos will go live unreviewed"
        )

    # --- The application's own guard ---------------------------------------
    # Authoritative: these are the exact checks the API runs at boot.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
        import os

        for key, value in env.items():
            os.environ[key] = value
        os.environ["APP_ENV"] = "production"
        os.environ["DEBUG"] = "false"
        os.environ.setdefault("ALLOWED_HOSTS", env.get("API_DOMAIN", ""))
        os.environ["EMAIL_CONSOLE_FALLBACK"] = "false"

        from app.core.config import Settings  # noqa: PLC0415

        Settings(_env_file=None).assert_production_ready()
    except RuntimeError as exc:
        for line in str(exc).splitlines():
            line = line.strip().lstrip("- ").strip()
            if line and not line.startswith("Refusing to start"):
                problems.append(f"[app] {line}")
    except ImportError:
        warnings.append(
            "Could not import the app to run its own production checks "
            "(install backend/requirements.txt to enable them)"
        )

    # --- Report -------------------------------------------------------------
    for warning in warnings:
        print(f"WARN   {warning}")
    for problem in problems:
        print(f"FAIL   {problem}")

    if problems:
        print(f"\n{len(problems)} problem(s) must be fixed before deploying.")
        return 1

    print(f"\nConfiguration looks deployable ({len(warnings)} warning(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
