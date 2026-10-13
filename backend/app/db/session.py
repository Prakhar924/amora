"""Async engine / session factory and the FastAPI session dependency.

Pooler awareness
----------------
Managed Postgres providers usually sit behind a transaction-mode pooler
(Supabase's Supavisor on port 6543, PgBouncer elsewhere). That mode hands each
transaction whichever backend connection happens to be free, which breaks two
assumptions asyncpg makes by default:

* **Prepared statements** are cached per connection. A statement prepared on
  one backend may not exist on the next, surfacing as intermittent
  `InvalidSQLStatementNameError` or `DuplicatePreparedStatementError` — and
  only under concurrency, so it passes every local test and fails in
  production.
* **Client-side pooling** is redundant and actively harmful: the provider is
  already pooling, and holding connections open burns a quota that is small on
  free plans.

So when a pooler is detected, prepared-statement caching is switched off and
SQLAlchemy's own pool is replaced with `NullPool`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def _engine_kwargs() -> dict[str, Any]:
    """Engine options appropriate to the database we are actually talking to."""
    if settings.uses_transaction_pooler:
        return {
            # The provider pools for us; a second pool just holds connections
            # open against a quota that is often tiny on free plans.
            "poolclass": NullPool,
            "connect_args": {
                # Disable asyncpg's prepared-statement cache entirely.
                "statement_cache_size": 0,
                # SQLAlchemy also prepares statements of its own; naming them
                # uniquely stops two clients colliding on one backend
                # connection. Returning "" makes asyncpg use unnamed
                # statements, which transaction pooling handles correctly.
                "prepared_statement_name_func": lambda: "",
            },
        }

    # A database we own: pool normally.
    return {
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    }


engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    **_engine_kwargs(),
)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Rolls back on an unhandled exception."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
