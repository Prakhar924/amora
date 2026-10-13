"""Redis-backed fixed-window rate limiting.

Abuse limits are a safety feature here, not just capacity protection: they are
what stops credential stuffing on login and message-spam across matches.

The counter uses a single atomic INCR plus an EXPIRE on first write. If Redis
is unavailable the limiter *fails open* and logs loudly — a Redis outage
should degrade protection, not lock every user out of the product.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


@dataclass(frozen=True)
class Limit:
    max_requests: int
    window_seconds: int

    @classmethod
    def parse(cls, spec: str) -> Limit:
        """Parse a `"requests/seconds"` spec such as `"10/300"`."""
        requests, _, window = spec.partition("/")
        return cls(int(requests), int(window))


async def consume(bucket: str, identity: str, limit: Limit) -> tuple[bool, int]:
    """Count one hit. Returns `(allowed, seconds_until_reset)`."""
    key = f"rl:{bucket}:{identity}"
    try:
        client = get_redis()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = await pipe.execute()
        if count == 1 or ttl < 0:
            await client.expire(key, limit.window_seconds)
            ttl = limit.window_seconds
        return count <= limit.max_requests, int(ttl)
    except Exception as exc:
        logger.error("Rate limiter unavailable, allowing request: %s", exc)
        return True, 0


def client_identity(request: Request) -> str:
    """Best-effort caller identity for unauthenticated endpoints.

    Honours `X-Forwarded-For` only when the app is explicitly behind a proxy,
    since the header is trivially spoofable when it is not.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and settings.app_env == "production":
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_user_limit(bucket: str, user_id, spec: str) -> None:
    """Apply a limit keyed to an authenticated user.

    Called from inside a handler rather than as a dependency, because FastAPI
    resolves router-level dependencies before the auth dependency has run and
    so cannot key on the user yet.
    """
    allowed, retry_after = await consume(bucket, str(user_id), Limit.parse(spec))
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down and try again shortly.",
            headers={"Retry-After": str(max(1, retry_after))},
        )


def rate_limit(bucket: str, spec: str):
    """FastAPI dependency factory applying a named limit to an endpoint."""
    limit = Limit.parse(spec)

    async def dependency(request: Request) -> None:
        # Prefer the authenticated subject so one abusive account cannot
        # exhaust the budget of everyone sharing its NAT.
        identity = getattr(request.state, "user_id", None) or client_identity(request)
        allowed, retry_after = await consume(bucket, str(identity), limit)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down and try again shortly.",
                headers={"Retry-After": str(max(1, retry_after))},
            )

    return dependency
