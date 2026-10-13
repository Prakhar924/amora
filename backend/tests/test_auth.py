"""Authentication: registration, the 18+ gate, login, refresh rotation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_email_token
from app.models import User, UserPreference, UserStatus

API = settings.api_prefix


def _registration(**overrides) -> dict:
    payload = {
        "email": "new.user@amora.dev",
        "password": "GoodPass123",
        "display_name": "New User",
        "date_of_birth": "1996-04-02",
        "gender": "woman",
        "accept_terms": True,
        "accept_privacy": True,
    }
    payload.update(overrides)
    return payload


class TestRegistration:
    async def test_creates_user_profile_and_preferences(
        self, client: AsyncClient, session_factory
    ):
        response = await client.post(f"{API}/auth/register", json=_registration())
        assert response.status_code == 202, response.text

        async with session_factory() as db:
            user = (
                await db.execute(
                    select(User).where(User.email == "new.user@amora.dev")
                )
            ).scalar_one()
            assert user.status == UserStatus.PENDING_VERIFICATION
            assert user.email_verified_at is None
            # Consent must be recorded, not merely accepted in the request.
            assert user.terms_accepted_at is not None
            assert user.privacy_accepted_at is not None
            # The password must never be recoverable from the row.
            assert "GoodPass123" not in user.hashed_password

            pref = (
                await db.execute(
                    select(UserPreference).where(UserPreference.user_id == user.id)
                )
            ).scalar_one()
            assert pref.min_age == settings.minimum_age

    async def test_email_is_normalized_to_lowercase(self, client: AsyncClient):
        await client.post(
            f"{API}/auth/register", json=_registration(email="MiXeD@Amora.DEV")
        )
        login = await client.post(
            f"{API}/auth/login",
            json={"email": "mixed@amora.dev", "password": "GoodPass123"},
        )
        # Still 403 (unverified), but that proves the account was found.
        assert login.status_code == 403

    @pytest.mark.parametrize("years_old", [0, 10, 17])
    async def test_rejects_under_18(self, client: AsyncClient, years_old: int):
        dob = date.today() - timedelta(days=years_old * 365 + 5)
        response = await client.post(
            f"{API}/auth/register",
            json=_registration(date_of_birth=dob.isoformat()),
        )
        assert response.status_code == 422
        assert "18" in response.json()["detail"]

    async def test_accepts_exactly_18_today(self, client: AsyncClient):
        """The boundary must be inclusive: an 18th birthday is eligible."""
        dob = date.today().replace(year=date.today().year - 18)
        response = await client.post(
            f"{API}/auth/register",
            json=_registration(
                email="exactly18@amora.dev", date_of_birth=dob.isoformat()
            ),
        )
        assert response.status_code == 202, response.text

    async def test_rejects_day_before_18th_birthday(self, client: AsyncClient):
        dob = date.today().replace(year=date.today().year - 18) + timedelta(days=1)
        response = await client.post(
            f"{API}/auth/register",
            json=_registration(
                email="almost18@amora.dev", date_of_birth=dob.isoformat()
            ),
        )
        assert response.status_code == 422

    async def test_rejects_future_date_of_birth(self, client: AsyncClient):
        dob = date.today() + timedelta(days=1)
        response = await client.post(
            f"{API}/auth/register", json=_registration(date_of_birth=dob.isoformat())
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "field", ["accept_terms", "accept_privacy"]
    )
    async def test_requires_both_consents(self, client: AsyncClient, field: str):
        response = await client.post(
            f"{API}/auth/register", json=_registration(**{field: False})
        )
        assert response.status_code == 422
        assert "accept" in response.json()["detail"].lower()

    @pytest.mark.parametrize(
        "password", ["short1", "nodigitshere", "1234567890", "password123"]
    )
    async def test_rejects_weak_passwords(self, client: AsyncClient, password: str):
        response = await client.post(
            f"{API}/auth/register", json=_registration(password=password)
        )
        assert response.status_code == 422

    async def test_duplicate_email_does_not_leak_existence(
        self, client: AsyncClient, session_factory
    ):
        first = await client.post(f"{API}/auth/register", json=_registration())
        second = await client.post(f"{API}/auth/register", json=_registration())
        # Identical status and no "already exists" wording.
        assert first.status_code == second.status_code == 202
        assert "exist" not in second.text.lower()
        assert "taken" not in second.text.lower()

        async with session_factory() as db:
            count = len(
                (
                    await db.execute(
                        select(User).where(User.email == "new.user@amora.dev")
                    )
                )
                .scalars()
                .all()
            )
            assert count == 1


class TestEmailVerification:
    async def test_verification_activates_the_account(
        self, client: AsyncClient, users, session_factory
    ):
        user = await users.create(verified=False, status=UserStatus.PENDING_VERIFICATION)
        token = create_email_token(user.id, "verify_email")

        response = await client.post(f"{API}/auth/verify-email", json={"token": token})
        assert response.status_code == 200

        async with session_factory() as db:
            refreshed = (
                await db.execute(select(User).where(User.id == user.id))
            ).scalar_one()
            assert refreshed.status == UserStatus.ACTIVE
            assert refreshed.email_verified_at is not None

    async def test_a_reset_token_cannot_verify_email(
        self, client: AsyncClient, users
    ):
        """Token purposes must not be interchangeable."""
        user = await users.create(verified=False)
        wrong = create_email_token(user.id, "reset_password")
        response = await client.post(f"{API}/auth/verify-email", json={"token": wrong})
        assert response.status_code == 400

    async def test_garbage_token_is_rejected(self, client: AsyncClient):
        response = await client.post(
            f"{API}/auth/verify-email", json={"token": "not-a-jwt"}
        )
        assert response.status_code == 400


class TestLogin:
    async def test_login_returns_a_token_pair(self, client: AsyncClient, users):
        user = await users.create()
        response = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "TestPass123!"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["access_token"] and body["refresh_token"]
        assert body["expires_in"] == settings.access_token_ttl_minutes * 60

    async def test_wrong_password_and_unknown_email_are_indistinguishable(
        self, client: AsyncClient, users
    ):
        user = await users.create()
        wrong_password = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "WrongPass123!"},
        )
        unknown_email = await client.post(
            f"{API}/auth/login",
            json={"email": "nobody@amora.dev", "password": "WrongPass123!"},
        )
        assert wrong_password.status_code == unknown_email.status_code == 401
        assert wrong_password.json()["detail"] == unknown_email.json()["detail"]

    async def test_unverified_user_cannot_log_in(self, client: AsyncClient, users):
        user = await users.create(
            verified=False, status=UserStatus.PENDING_VERIFICATION
        )
        response = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "TestPass123!"},
        )
        assert response.status_code == 403
        assert "confirm" in response.json()["detail"].lower()

    async def test_login_is_rate_limited(
        self, client: AsyncClient, users, rate_limited
    ):
        user = await users.create()
        limit = int(settings.rate_limit_login.split("/")[0])
        statuses = []
        for _ in range(limit + 2):
            response = await client.post(
                f"{API}/auth/login",
                json={"email": user.email, "password": "WrongPass123!"},
            )
            statuses.append(response.status_code)
        assert 429 in statuses, "brute-force attempts were never throttled"


class TestRefreshRotation:
    async def test_refresh_rotates_the_token(self, client: AsyncClient, users):
        user = await users.create()
        login = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "TestPass123!"},
        )
        original = login.json()["refresh_token"]

        response = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": original}
        )
        assert response.status_code == 200
        rotated = response.json()["refresh_token"]
        assert rotated != original, "refresh token was not rotated"

    async def test_reusing_a_consumed_token_revokes_the_family(
        self, client: AsyncClient, users
    ):
        """Replay is treated as theft: the whole session family dies."""
        user = await users.create()
        login = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "TestPass123!"},
        )
        first = login.json()["refresh_token"]

        rotated = (
            await client.post(f"{API}/auth/refresh", json={"refresh_token": first})
        ).json()["refresh_token"]

        # Replay the already-consumed token.
        replay = await client.post(f"{API}/auth/refresh", json={"refresh_token": first})
        assert replay.status_code == 401

        # The legitimately-rotated token must now be dead too.
        after = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": rotated}
        )
        assert after.status_code == 401, "token family was not revoked after replay"

    async def test_unknown_refresh_token_is_rejected(self, client: AsyncClient):
        response = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": "nope"}
        )
        assert response.status_code == 401


class TestPasswordChange:
    async def test_changing_password_invalidates_existing_tokens(
        self, client: AsyncClient, users, auth
    ):
        user = await users.create()
        headers = await auth(user)

        assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 200

        changed = await client.post(
            f"{API}/auth/change-password",
            headers=headers,
            json={
                "current_password": "TestPass123!",
                "new_password": "BrandNewPass456",
            },
        )
        assert changed.status_code == 200

        # token_version was bumped, so the old access token is now stale.
        after = await client.get(f"{API}/auth/me", headers=headers)
        assert after.status_code == 401

    async def test_wrong_current_password_is_rejected(
        self, client: AsyncClient, users, auth
    ):
        user = await users.create()
        headers = await auth(user)
        response = await client.post(
            f"{API}/auth/change-password",
            headers=headers,
            json={
                "current_password": "NotMyPassword1",
                "new_password": "BrandNewPass456",
            },
        )
        assert response.status_code == 400


class TestForgotPassword:
    async def test_response_is_identical_for_unknown_address(
        self, client: AsyncClient, users
    ):
        user = await users.create()
        known = await client.post(
            f"{API}/auth/forgot-password", json={"email": user.email}
        )
        unknown = await client.post(
            f"{API}/auth/forgot-password", json={"email": "ghost@amora.dev"}
        )
        assert known.status_code == unknown.status_code == 202
        assert known.json() == unknown.json()

    async def test_reset_token_sets_a_new_password(
        self, client: AsyncClient, users
    ):
        user = await users.create()
        token = create_email_token(user.id, "reset_password", hours=1)

        response = await client.post(
            f"{API}/auth/reset-password",
            json={"token": token, "new_password": "FreshPass789"},
        )
        assert response.status_code == 200

        login = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "FreshPass789"},
        )
        assert login.status_code == 200

    async def test_old_password_stops_working_after_reset(
        self, client: AsyncClient, users
    ):
        user = await users.create()
        token = create_email_token(user.id, "reset_password", hours=1)
        await client.post(
            f"{API}/auth/reset-password",
            json={"token": token, "new_password": "FreshPass789"},
        )
        login = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "TestPass123!"},
        )
        assert login.status_code == 401
