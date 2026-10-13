"""Behaviour specific to the free-tier (Render + Supabase + Upstash) deployment.

These paths never execute locally, so without tests they would first run in
production. Covered here:

* Connection-URL handling for a managed provider, including the transaction
  pooler settings that prevent intermittent prepared-statement failures.
* The Resend HTTPS transport, which is mandatory on Render because outbound
  SMTP ports are blocked.
* Registration and login with email verification switched off — the trade-off
  that makes a $0 deployment usable without a custom sending domain.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import NullPool

from app.core.config import Settings
from app.core.config import settings as live_settings

# Captured at import time, before conftest's autouse `_no_email` fixture
# replaces the module attribute with a capturing stub. These tests exercise
# the real delivery logic, so they need the genuine function.
from app.services.email import send_email as real_send_email

API = live_settings.api_prefix

SUPABASE_POOLER = (
    "postgresql://postgres.abcdefgh:s3cret"
    "@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"
)


class TestManagedDatabaseUrls:
    def test_provider_url_is_rewritten_for_asyncpg(self):
        s = Settings(_env_file=None, database_url_override=SUPABASE_POOLER)
        assert s.database_url.startswith("postgresql+asyncpg://")
        assert "pooler.supabase.com:6543" in s.database_url

    def test_provider_url_is_rewritten_for_alembic(self):
        s = Settings(_env_file=None, database_url_override=SUPABASE_POOLER)
        assert s.sync_database_url.startswith("postgresql+psycopg://")

    def test_legacy_postgres_scheme_is_accepted(self):
        """Some providers still hand out `postgres://`, which SQLAlchemy rejects."""
        s = Settings(
            _env_file=None, database_url_override="postgres://u:p@host:5432/db"
        )
        assert s.database_url == "postgresql+asyncpg://u:p@host:5432/db"

    def test_discrete_settings_are_used_when_no_url_is_given(self):
        s = Settings(_env_file=None, postgres_host="db.internal", postgres_db="amora")
        assert "db.internal" in s.database_url
        assert s.database_url_override is None

    def test_credentials_are_not_logged_by_the_url_helpers(self):
        """A connection URL must never be printed whole; entrypoint redacts it."""
        s = Settings(_env_file=None, database_url_override=SUPABASE_POOLER)
        redacted = s.sync_database_url.split("@")[-1]
        assert "s3cret" not in redacted


class TestTransactionPoolerDetection:
    @pytest.mark.parametrize(
        "url",
        [
            SUPABASE_POOLER,
            "postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
            "postgresql://u:p@my-pgbouncer.internal:6543/db",
        ],
    )
    def test_pooled_urls_are_detected(self, url: str):
        assert Settings(_env_file=None, database_url_override=url).uses_transaction_pooler

    def test_a_direct_connection_is_not_treated_as_pooled(self):
        s = Settings(
            _env_file=None, database_url_override="postgresql://u:p@db.host:5432/amora"
        )
        assert not s.uses_transaction_pooler

    def test_detection_can_be_forced(self):
        s = Settings(
            _env_file=None,
            database_url_override="postgresql://u:p@db.host:5432/amora",
            force_pooled_database=True,
        )
        assert s.uses_transaction_pooler

    def test_pooled_engine_disables_prepared_statements_and_pooling(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The specific configuration that avoids Supabase's pooler failure.

        Without `statement_cache_size=0`, asyncpg caches prepared statements
        per connection. A transaction pooler reassigns connections between
        transactions, so the cached statement may not exist on the next one —
        an error that appears only under concurrency, i.e. in production.
        """
        from app.db import session as session_module

        monkeypatch.setattr(live_settings, "force_pooled_database", True)
        kwargs = session_module._engine_kwargs()

        assert kwargs["poolclass"] is NullPool
        assert kwargs["connect_args"]["statement_cache_size"] == 0
        # Unnamed statements are safe to reuse across pooled connections.
        assert kwargs["connect_args"]["prepared_statement_name_func"]() == ""

    def test_unpooled_engine_keeps_normal_pooling(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from app.db import session as session_module

        monkeypatch.setattr(live_settings, "force_pooled_database", False)
        monkeypatch.setattr(live_settings, "database_url_override", None)
        kwargs = session_module._engine_kwargs()

        assert "poolclass" not in kwargs
        assert kwargs["pool_pre_ping"] is True


class TestEmailTransportSelection:
    def test_resend_is_chosen_when_an_api_key_is_present(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from app.services import email as email_service

        monkeypatch.setattr(live_settings, "email_transport", "auto")
        monkeypatch.setattr(live_settings, "resend_api_key", "re_test_key")
        assert email_service._resolve_transport() == "resend"

    def test_smtp_is_chosen_without_an_api_key(self, monkeypatch: pytest.MonkeyPatch):
        from app.services import email as email_service

        monkeypatch.setattr(live_settings, "email_transport", "auto")
        monkeypatch.setattr(live_settings, "resend_api_key", None)
        monkeypatch.setattr(live_settings, "smtp_host", "smtp.example.com")
        assert email_service._resolve_transport() == "smtp"

    def test_an_explicit_transport_overrides_detection(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from app.services import email as email_service

        monkeypatch.setattr(live_settings, "email_transport", "console")
        monkeypatch.setattr(live_settings, "resend_api_key", "re_test_key")
        assert email_service._resolve_transport() == "console"


class TestResendTransport:
    """The HTTPS transport, which is the only one that works on Render."""

    async def test_a_successful_send_posts_the_expected_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from app.services import email as email_service

        captured: dict = {}

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def post(self, url, headers=None, json=None):
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
                return httpx.Response(200, json={"id": "abc"})

        monkeypatch.setattr(email_service.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(live_settings, "resend_api_key", "re_test_key")
        monkeypatch.setattr(live_settings, "smtp_from", "no-reply@amora.app")

        await email_service._send_via_resend(
            "user@example.com", "Subject", "Body", "<p>Body</p>"
        )

        assert captured["url"] == email_service.RESEND_ENDPOINT
        assert captured["headers"]["Authorization"] == "Bearer re_test_key"
        assert captured["json"]["to"] == ["user@example.com"]
        assert captured["json"]["from"] == "no-reply@amora.app"
        assert captured["json"]["html"] == "<p>Body</p>"

    async def test_the_shared_sender_restriction_is_explained(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Resend's 403 is opaque; the message must name the real cause."""
        from app.services import email as email_service

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def post(self, *_args, **_kwargs):
                return httpx.Response(403, text="not allowed")

        monkeypatch.setattr(email_service.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(live_settings, "resend_api_key", "re_test_key")
        monkeypatch.setattr(live_settings, "smtp_from", "onboarding@resend.dev")

        with pytest.raises(RuntimeError) as caught:
            await email_service._send_via_resend("other@example.com", "s", "b", None)

        message = str(caught.value)
        assert "verify a domain" in message
        assert "owns the Resend account" in message

    async def test_failure_propagates_when_the_fallback_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """In production a failed send must surface, not vanish into a log."""
        from app.services import email as email_service

        async def boom(*_args, **_kwargs):
            raise RuntimeError("transport exploded")

        monkeypatch.setattr(email_service, "_send_via_resend", boom)
        monkeypatch.setattr(live_settings, "email_transport", "resend")
        monkeypatch.setattr(live_settings, "email_console_fallback", False)

        with pytest.raises(RuntimeError):
            await real_send_email("u@example.com", "s", "b")

    async def test_failure_is_swallowed_when_the_fallback_is_on(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Development without mail configured must still work."""
        from app.services import email as email_service

        async def boom(*_args, **_kwargs):
            raise RuntimeError("transport exploded")

        monkeypatch.setattr(email_service, "_send_via_resend", boom)
        monkeypatch.setattr(live_settings, "email_transport", "resend")
        monkeypatch.setattr(live_settings, "email_console_fallback", True)

        # Must not raise: a developer machine with no mail configured still works.
        await real_send_email("u@example.com", "s", "b")


class TestRegistrationWithoutEmailVerification:
    """The $0-without-a-domain path: usable, and explicitly less safe."""

    async def test_account_is_active_immediately(
        self, client, session_factory, monkeypatch: pytest.MonkeyPatch
    ):
        from sqlalchemy import select

        from app.models import User, UserStatus

        monkeypatch.setattr(live_settings, "require_email_verification", False)

        response = await client.post(
            f"{API}/auth/register",
            json={
                "email": "nodomain@amora.dev",
                "password": "GoodPass123",
                "display_name": "No Domain",
                "date_of_birth": "1995-05-05",
                "gender": "woman",
                "accept_terms": True,
                "accept_privacy": True,
            },
        )
        assert response.status_code == 202
        assert response.json()["verification_required"] is False

        async with session_factory() as db:
            user = (
                await db.execute(select(User).where(User.email == "nodomain@amora.dev"))
            ).scalar_one()
            assert user.status == UserStatus.ACTIVE
            assert user.email_verified_at is not None

    async def test_the_user_can_sign_in_and_use_the_api(
        self, client, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(live_settings, "require_email_verification", False)

        await client.post(
            f"{API}/auth/register",
            json={
                "email": "signin@amora.dev",
                "password": "GoodPass123",
                "display_name": "Sign In",
                "date_of_birth": "1995-05-05",
                "gender": "man",
                "accept_terms": True,
                "accept_privacy": True,
            },
        )
        login = await client.post(
            f"{API}/auth/login",
            json={"email": "signin@amora.dev", "password": "GoodPass123"},
        )
        assert login.status_code == 200

        me = await client.get(
            f"{API}/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["email_verified"] is True

    async def test_verification_still_gates_when_enabled(
        self, client, users, monkeypatch: pytest.MonkeyPatch
    ):
        """The default must remain the safe one."""
        from app.models import UserStatus

        monkeypatch.setattr(live_settings, "require_email_verification", True)
        user = await users.create(
            verified=False, status=UserStatus.PENDING_VERIFICATION
        )
        response = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "TestPass123!"},
        )
        assert response.status_code == 403


class TestSingleInstanceFanout:
    async def test_fanout_is_skipped_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """On a single instance, publishing to Redis is a wasted round trip."""
        from app.ws.manager import ConnectionManager

        monkeypatch.setattr(live_settings, "ws_fanout_enabled", False)
        manager = ConnectionManager()
        await manager.start()

        # No Redis connection was opened, and delivery still works in-process.
        assert manager._redis is None
        await manager.stop()

    async def test_local_delivery_works_without_redis(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import uuid

        from app.ws.manager import ConnectionManager

        monkeypatch.setattr(live_settings, "ws_fanout_enabled", False)
        manager = ConnectionManager()
        await manager.start()

        delivered: list[dict] = []

        class FakeSocket:
            async def send_json(self, payload):
                delivered.append(payload)

        user_id = uuid.uuid4()
        manager._sockets[user_id].add(FakeSocket())
        await manager.send_to_user(user_id, {"type": "message.created"})

        assert delivered == [{"type": "message.created"}]
        await manager.stop()
