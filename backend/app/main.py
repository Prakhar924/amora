"""FastAPI application factory.

Mounts every router under `settings.api_prefix`, except the WebSocket, which
lives at a stable top-level path so clients do not have to version their
socket URL.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routers import (
    admin,
    auth,
    discovery,
    matches,
    messages,
    profiles,
    safety,
    ws,
)
from app.core.config import settings
from app.core.ratelimit import close_redis, get_redis
from app.db.session import engine
from app.services import storage
from app.ws.manager import manager

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

# Third-party libraries are extremely verbose at DEBUG: botocore logs every
# request signature, which buries the application's own output. Pin them to
# WARNING so `docker compose logs api` stays readable in development.
for noisy in ("botocore", "boto3", "urllib3", "s3transfer", "asyncio", "aiobotocore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s API (%s)", settings.app_name, settings.app_env)

    # Fail loudly at boot rather than on the first request.
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection OK")
    except Exception as exc:
        logger.error("Database unreachable at startup: %s", exc)

    await manager.start()

    # Create the local bucket on first boot so development needs no manual
    # MinIO setup. In production the bucket is provisioned by infrastructure.
    if settings.app_env != "production":
        storage.ensure_bucket()

    yield

    await manager.stop()
    await close_redis()
    await engine.dispose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="1.0.0",
        summary="Dating platform API — discovery, matching, and real-time chat.",
        description=(
            "All endpoints except `/auth/*` and `/health` require a bearer "
            "access token. Tokens are short-lived; rotate them with "
            "`POST /auth/refresh`.\n\n"
            "**Privacy:** profile responses never contain coordinates or "
            "account data. Distances are rounded."
        ),
        lifespan=lifespan,
        # The OpenAPI schema is a map of the attack surface. It is invaluable
        # in development and pointless to publish alongside a consumer app, so
        # it is disabled in production unless ENABLE_DOCS is set.
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    # Reject requests carrying a Host header we do not recognise. Without this
    # an attacker can poison the absolute URLs we put in verification and
    # password-reset emails. Skipped when the allow-list is a wildcard, which
    # `assert_production_ready` forbids in production anyway.
    if settings.allowed_host_list != ["*"]:
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Baseline response hardening.

        Set here rather than only at the reverse proxy, so the API is still
        protected if it is ever exposed directly or fronted by a proxy that
        was not configured with these.
        """
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # This API serves JSON only; nothing should ever be rendered from it.
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Flatten Pydantic errors into messages a client can show directly.

        The default 422 body is a nested structure the mobile client would have
        to parse; this surfaces the first human-readable reason instead.
        """
        messages = []
        for error in exc.errors():
            location = ".".join(str(p) for p in error.get("loc", []) if p != "body")
            message = error.get("msg", "Invalid value")
            message = message.removeprefix("Value error, ")
            messages.append(f"{location}: {message}" if location else message)
        return JSONResponse(
            # Literal 422: Starlette renamed the constant and deprecated the
            # old spelling, so the number avoids depending on either name.
            status_code=422,
            content={"detail": messages[0] if messages else "Invalid request", "errors": messages},
        )

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    async def health() -> dict:
        """Cheap liveness check: is the process up and the database reachable?

        Used by the container healthcheck and the load balancer, so it stays
        light — no object-storage round trip.
        """
        checks = {"api": "ok"}
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "unavailable"
        healthy = all(v == "ok" for v in checks.values())
        return {"status": "ok" if healthy else "degraded", "checks": checks}

    @app.get("/health/ready", tags=["meta"], summary="Full dependency readiness")
    async def readiness() -> JSONResponse:
        """Verify every dependency the product actually needs.

        Kept separate from liveness because the right reaction differs: a
        failed liveness check should restart the container, whereas a failed
        readiness check should stop a release being promoted. Returns 503 when
        degraded, so a deploy script can just test the status code.
        """
        checks: dict[str, str] = {"api": "ok"}

        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            logger.error("Readiness: database unreachable: %s", exc)
            checks["database"] = "unavailable"

        try:
            await get_redis().ping()
            checks["redis"] = "ok"
        except Exception as exc:
            logger.error("Readiness: redis unreachable: %s", exc)
            checks["redis"] = "unavailable"

        # Photo upload and display both break without object storage, so a bad
        # bucket credential must not be reported as healthy.
        try:
            reachable = await anyio.to_thread.run_sync(storage.bucket_reachable)
            checks["object_storage"] = "ok" if reachable else "unavailable"
        except Exception as exc:
            logger.error("Readiness: object storage unreachable: %s", exc)
            checks["object_storage"] = "unavailable"

        healthy = all(v == "ok" for v in checks.values())
        # Returned explicitly rather than via an injected Response, because
        # this module uses `from __future__ import annotations` and FastAPI
        # cannot resolve the resulting forward reference.
        return JSONResponse(
            status_code=(
                status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            content={"status": "ok" if healthy else "degraded", "checks": checks},
        )

    prefix = settings.api_prefix
    app.include_router(auth.router, prefix=prefix)
    app.include_router(profiles.router, prefix=prefix)
    app.include_router(discovery.router, prefix=prefix)
    app.include_router(matches.router, prefix=prefix)
    app.include_router(messages.router, prefix=prefix)
    app.include_router(safety.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)
    # Unversioned: the socket protocol is negotiated by frame type instead.
    app.include_router(ws.router)

    return app


app = create_app()
