"""Test fixtures.

The suite runs against a real Postgres database (`<POSTGRES_DB>_test`), not
SQLite: the application depends on Postgres-specific behaviour — native enums,
`ARRAY`, `JSONB`, `ON CONFLICT`, and the UNIQUE constraint that makes matching
race-safe — so testing on another engine would not be testing the real thing.

Isolation: tables are truncated between tests rather than recreated, which is
much faster than per-test DDL.

External services are stubbed:
* Object storage — presigning is replaced, so no S3 is required.
* Rate limiting — allowed by default; opt back in with the `rate_limited`
  fixture.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import (
    Gender,
    Interest,
    ModerationStatus,
    Profile,
    ProfilePhoto,
    User,
    UserPreference,
    UserStatus,
)

TEST_DB = f"{settings.postgres_db}_test"
TEST_DB_URL = (
    f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
    f"@{settings.postgres_host}:{settings.postgres_port}/{TEST_DB}"
)
ADMIN_URL = (
    f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
    f"@{settings.postgres_host}:{settings.postgres_port}/postgres"
)

TABLES = [
    "message_reads", "messages", "conversations", "matches", "likes",
    "reports", "blocks", "notifications", "user_interests", "profile_photos",
    "user_preferences", "profiles", "sessions", "users", "interests",
]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: longer-running concurrency tests")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_test_database() -> AsyncIterator[None]:
    """Create the test database once, then build the schema from the models."""
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        exists = (
            await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DB},
            )
        ).first()
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    await admin.dispose()

    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        # `create_all` from the same metadata Alembic autogenerates from, so
        # the test schema matches what migrations produce.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield


@pytest_asyncio.fixture(scope="session")
async def engine():
    # A normal pool is safe because pytest-asyncio is configured to run the
    # whole session on one event loop, so a pooled asyncpg connection never
    # outlives its loop. Pooling matters here: reconnecting per statement made
    # the suite roughly an order of magnitude slower.
    eng = create_async_engine(TEST_DB_URL, pool_size=20, max_overflow=10)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine) -> AsyncIterator[None]:
    """Empty every table before each test."""
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
    yield


@pytest_asyncio.fixture
async def db(session_factory) -> AsyncIterator:
    async with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def _stub_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace object storage so tests never reach the network."""
    from app.services import storage

    monkeypatch.setattr(
        storage,
        "presign_upload",
        lambda key, content_type, byte_size: {
            "url": f"https://storage.test/{key}?signature=stub",
            "headers": {"Content-Type": content_type, "Content-Length": str(byte_size)},
            "expires_in": 900,
        },
    )
    monkeypatch.setattr(
        storage, "presign_download", lambda key: f"https://storage.test/{key}?sig=stub"
    )
    # Pretend any upload succeeded, so confirm-photo is testable.
    monkeypatch.setattr(storage, "object_exists", lambda key: (True, 2048))
    monkeypatch.setattr(storage, "delete_object", lambda key: None)
    monkeypatch.setattr(storage, "ensure_bucket", lambda: True)

    # The serializers import these by name, so patch them there too.
    from app.services import serializers

    monkeypatch.setattr(
        serializers, "presign_download", lambda key: f"https://storage.test/{key}?sig=stub"
    )


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable rate limiting by default; `rate_limited` re-enables it."""
    from app.core import ratelimit

    async def always_allow(bucket: str, identity: str, limit) -> tuple[bool, int]:
        return True, 0

    monkeypatch.setattr(ratelimit, "consume", always_allow)


@pytest.fixture
def rate_limited(monkeypatch: pytest.MonkeyPatch):
    """Re-enable rate limiting with an in-memory counter (no Redis needed)."""
    from app.core import ratelimit

    counters: dict[str, int] = {}

    async def counting(bucket: str, identity: str, limit) -> tuple[bool, int]:
        key = f"{bucket}:{identity}"
        counters[key] = counters.get(key, 0) + 1
        return counters[key] <= limit.max_requests, limit.window_seconds

    monkeypatch.setattr(ratelimit, "consume", counting)
    return counters


@pytest.fixture(autouse=True)
def _no_email(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture outbound email instead of sending it."""
    outbox: list[dict] = []

    async def capture(to, subject, text_body, html_body=None):
        outbox.append({"to": to, "subject": subject, "body": text_body})

    from app.services import email as email_service

    monkeypatch.setattr(email_service, "send_email", capture)
    return outbox


@pytest.fixture(autouse=True)
def _no_ws(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Capture WebSocket pushes so handlers can be asserted without a socket."""
    pushes: list[tuple] = []

    async def capture(user_id, payload):
        pushes.append((user_id, payload))

    from app.ws.manager import manager

    monkeypatch.setattr(manager, "send_to_user", capture)
    return pushes


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the app, with the test database injected."""

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", timeout=30
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Factories
# --------------------------------------------------------------------------
API = settings.api_prefix
PASSWORD = "TestPass123!"


class UserFactory:
    """Creates users directly in the database.

    Going through the API for setup would make every test depend on
    registration and email verification; those have their own tests.
    """

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._counter = 0

    async def create(
        self,
        *,
        email: str | None = None,
        password: str = PASSWORD,
        age: int = 30,
        gender: Gender = Gender.WOMAN,
        display_name: str | None = None,
        status: UserStatus = UserStatus.ACTIVE,
        verified: bool = True,
        with_photo: bool = True,
        photo_approved: bool = True,
        complete: bool = True,
        discoverable: bool = True,
        show_only_to_matches: bool = False,
        latitude: float | None = 12.9716,
        longitude: float | None = 77.5946,
        city: str | None = "Bengaluru",
        is_admin: bool = False,
        interested_in: list[str] | None = None,
        min_age: int = 18,
        max_age: int = 99,
        max_distance_km: int = 500,
    ) -> User:
        self._counter += 1
        now = datetime.now(UTC)
        email = email or f"user{self._counter}-{uuid.uuid4().hex[:6]}@amora.dev"
        display_name = display_name or f"User{self._counter}"

        async with self._session_factory() as db:
            user = User(
                email=email.lower(),
                hashed_password=hash_password(password),
                date_of_birth=now - timedelta(days=age * 365 + 10),
                status=status,
                email_verified_at=now if verified else None,
                is_admin=is_admin,
                terms_accepted_at=now,
                privacy_accepted_at=now,
                last_active_at=now,
            )
            db.add(user)
            await db.flush()

            profile = Profile(
                user_id=user.id,
                display_name=display_name,
                gender=gender,
                bio="Test profile",
                latitude=latitude,
                longitude=longitude,
                city=city,
                country="IN",
                is_discoverable=discoverable,
                show_only_to_matches=show_only_to_matches,
            )
            db.add(profile)
            await db.flush()

            if with_photo:
                db.add(
                    ProfilePhoto(
                        profile_id=profile.id,
                        storage_key=f"photos/{user.id}/0.png",
                        content_type="image/png",
                        byte_size=1024,
                        position=0,
                        moderation_status=(
                            ModerationStatus.APPROVED
                            if photo_approved
                            else ModerationStatus.PENDING
                        ),
                        upload_confirmed=True,
                    )
                )

            if complete and with_photo and photo_approved:
                profile.completed_at = now

            db.add(
                UserPreference(
                    user_id=user.id,
                    interested_in=interested_in or [],
                    min_age=min_age,
                    max_age=max_age,
                    max_distance_km=max_distance_km,
                )
            )
            await db.commit()
            await db.refresh(user)
            # Detach so the object is usable after the session closes.
            db.expunge_all()
            return user


@pytest_asyncio.fixture
async def users(session_factory) -> UserFactory:
    return UserFactory(session_factory)


@pytest_asyncio.fixture
async def auth(client: AsyncClient):
    """Return a helper that logs a user in and yields auth headers."""

    async def _login(user: User, password: str = PASSWORD) -> dict[str, str]:
        response = await client.post(
            f"{API}/auth/login", json={"email": user.email, "password": password}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest_asyncio.fixture
async def interests(session_factory) -> list[Interest]:
    """A minimal interest vocabulary for profile-update tests."""
    async with session_factory() as db:
        rows = [
            Interest(slug="hiking", label="Hiking", category="outdoors"),
            Interest(slug="coffee", label="Coffee", category="food"),
            Interest(slug="jazz", label="Jazz", category="music"),
        ]
        db.add_all(rows)
        await db.commit()
        for row in rows:
            await db.refresh(row)
        db.expunge_all()
        return rows
