"""Blocking, reporting, moderation, profiles, and account deletion."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.config import settings
from app.models import (
    Block,
    Match,
    Message,
    Profile,
    ProfilePhoto,
    Report,
    ReportStatus,
    User,
    UserStatus,
)

API = settings.api_prefix


async def make_match(client: AsyncClient, auth, a, b) -> str:
    await client.post(
        f"{API}/discovery/swipe",
        headers=await auth(a),
        json={"target_user_id": str(b.id), "action": "like"},
    )
    response = await client.post(
        f"{API}/discovery/swipe",
        headers=await auth(b),
        json={"target_user_id": str(a.id), "action": "like"},
    )
    return response.json()["match"]["conversation_id"]


class TestBlocking:
    async def test_block_records_and_unmatches(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()
        await make_match(client, auth, a, b)

        response = await client.post(
            f"{API}/safety/block",
            headers=await auth(b),
            json={"user_id": str(a.id), "reason": "rude"},
        )
        assert response.status_code == 201

        async with session_factory() as db:
            block = (await db.execute(select(Block))).scalar_one()
            assert block.blocker_id == b.id
            assert block.blocked_id == a.id
            # The match is dissolved, not merely hidden.
            match = (await db.execute(select(Match))).scalar_one()
            assert match.unmatched_at is not None

    async def test_blocking_is_idempotent(self, client: AsyncClient, users, auth, session_factory):
        a = await users.create()
        b = await users.create()
        headers = await auth(a)

        for _ in range(3):
            response = await client.post(
                f"{API}/safety/block", headers=headers, json={"user_id": str(b.id)}
            )
            assert response.status_code == 201

        async with session_factory() as db:
            assert (await db.execute(select(func.count(Block.id)))).scalar_one() == 1

    async def test_cannot_block_self(self, client: AsyncClient, users, auth):
        a = await users.create()
        response = await client.post(
            f"{API}/safety/block", headers=await auth(a), json={"user_id": str(a.id)}
        )
        assert response.status_code == 400

    async def test_blocked_profile_is_not_viewable(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        await client.post(
            f"{API}/safety/block",
            headers=await auth(b),
            json={"user_id": str(a.id)},
        )

        # Neither direction can see the other.
        assert (
            await client.get(f"{API}/profiles/{b.id}", headers=await auth(a))
        ).status_code == 404
        assert (
            await client.get(f"{API}/profiles/{a.id}", headers=await auth(b))
        ).status_code == 404

    async def test_block_list_and_unblock(self, client: AsyncClient, users, auth):
        a = await users.create()
        b = await users.create(display_name="Blocked")
        headers = await auth(a)

        await client.post(
            f"{API}/safety/block", headers=headers, json={"user_id": str(b.id)}
        )
        listing = await client.get(f"{API}/safety/blocks", headers=headers)
        assert len(listing.json()) == 1
        assert listing.json()[0]["display_name"] == "Blocked"

        removed = await client.delete(f"{API}/safety/block/{b.id}", headers=headers)
        assert removed.status_code == 200
        assert (await client.get(f"{API}/safety/blocks", headers=headers)).json() == []

    async def test_unblocking_someone_not_blocked_is_404(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        response = await client.delete(
            f"{API}/safety/block/{b.id}", headers=await auth(a)
        )
        assert response.status_code == 404


class TestReporting:
    async def test_report_is_queued_for_review(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()

        response = await client.post(
            f"{API}/safety/report",
            headers=await auth(a),
            json={
                "user_id": str(b.id),
                "reason": "harassment",
                "details": "Sent abusive messages",
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "open"

        async with session_factory() as db:
            report = (await db.execute(select(Report))).scalar_one()
            assert report.reporter_id == a.id
            assert report.reported_id == b.id
            assert report.status == ReportStatus.OPEN

    async def test_cannot_report_self(self, client: AsyncClient, users, auth):
        a = await users.create()
        response = await client.post(
            f"{API}/safety/report",
            headers=await auth(a),
            json={"user_id": str(a.id), "reason": "spam"},
        )
        assert response.status_code == 400

    async def test_cannot_report_a_message_from_a_foreign_conversation(
        self, client: AsyncClient, users, auth
    ):
        """A report must not be a way to expose strangers' private messages."""
        a = await users.create()
        b = await users.create()
        outsider = await users.create()
        conversation = await make_match(client, auth, a, b)

        sent = await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(a),
            json={"body": "private between a and b"},
        )
        message_id = sent.json()["id"]

        response = await client.post(
            f"{API}/safety/report",
            headers=await auth(outsider),
            json={
                "user_id": str(a.id),
                "reason": "harassment",
                "message_id": message_id,
            },
        )
        assert response.status_code == 404

    async def test_participant_can_report_a_message(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)

        sent = await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(a),
            json={"body": "something reportable"},
        )

        response = await client.post(
            f"{API}/safety/report",
            headers=await auth(b),
            json={
                "user_id": str(a.id),
                "reason": "harassment",
                "message_id": sent.json()["id"],
            },
        )
        assert response.status_code == 201

    async def test_reports_are_rate_limited(
        self, client: AsyncClient, users, auth, rate_limited
    ):
        a = await users.create()
        headers = await auth(a)
        limit = int(settings.rate_limit_report.split("/")[0])

        statuses = []
        for _ in range(limit + 2):
            target = await users.create()
            response = await client.post(
                f"{API}/safety/report",
                headers=headers,
                json={"user_id": str(target.id), "reason": "spam"},
            )
            statuses.append(response.status_code)
        assert 429 in statuses


class TestAdminModeration:
    async def test_admin_surface_is_hidden_from_regular_users(
        self, client: AsyncClient, users, auth
    ):
        regular = await users.create()
        headers = await auth(regular)

        for path in ("/admin/stats", "/admin/reports", "/admin/photos", "/admin/users"):
            response = await client.get(f"{API}{path}", headers=headers)
            # 404 rather than 403, so the surface is not advertised.
            assert response.status_code == 404, path

    async def test_admin_can_read_the_report_queue(
        self, client: AsyncClient, users, auth
    ):
        admin = await users.create(is_admin=True, with_photo=False, complete=False)
        reporter = await users.create()
        offender = await users.create(display_name="Offender")

        await client.post(
            f"{API}/safety/report",
            headers=await auth(reporter),
            json={"user_id": str(offender.id), "reason": "spam", "details": "bot"},
        )

        queue = await client.get(f"{API}/admin/reports", headers=await auth(admin))
        assert queue.status_code == 200
        rows = queue.json()
        assert len(rows) == 1
        assert rows[0]["reported_display_name"] == "Offender"
        assert rows[0]["report_count_against_reported"] == 1

    async def test_resolving_a_report_can_suspend_the_account(
        self, client: AsyncClient, users, auth, session_factory
    ):
        admin = await users.create(is_admin=True, with_photo=False, complete=False)
        reporter = await users.create()
        offender = await users.create()

        report = await client.post(
            f"{API}/safety/report",
            headers=await auth(reporter),
            json={"user_id": str(offender.id), "reason": "harassment"},
        )
        report_id = report.json()["id"]

        resolved = await client.post(
            f"{API}/admin/reports/{report_id}/resolve",
            headers=await auth(admin),
            json={
                "status": "actioned",
                "resolution_note": "Confirmed abuse",
                "suspend_days": 7,
            },
        )
        assert resolved.status_code == 200

        async with session_factory() as db:
            refreshed = (
                await db.execute(select(User).where(User.id == offender.id))
            ).scalar_one()
            assert refreshed.status == UserStatus.SUSPENDED
            assert refreshed.suspended_until is not None

    async def test_suspended_user_is_locked_out(
        self, client: AsyncClient, users, auth
    ):
        admin = await users.create(is_admin=True, with_photo=False, complete=False)
        offender = await users.create()
        offender_headers = await auth(offender)

        assert (
            await client.get(f"{API}/discovery", headers=offender_headers)
        ).status_code == 200

        await client.post(
            f"{API}/admin/users/{offender.id}/suspend",
            headers=await auth(admin),
            json={"days": 30, "reason": "Repeated abuse"},
        )

        # Suspension bumps token_version, so the live token dies immediately.
        response = await client.get(f"{API}/discovery", headers=offender_headers)
        assert response.status_code == 401

        relogin = await client.post(
            f"{API}/auth/login",
            json={"email": offender.email, "password": "TestPass123!"},
        )
        token = relogin.json()["access_token"]
        blocked = await client.get(
            f"{API}/discovery", headers={"Authorization": f"Bearer {token}"}
        )
        assert blocked.status_code == 403
        assert "suspend" in blocked.json()["detail"].lower()

    async def test_admins_cannot_be_suspended(
        self, client: AsyncClient, users, auth
    ):
        admin = await users.create(is_admin=True, with_photo=False, complete=False)
        other_admin = await users.create(is_admin=True, with_photo=False, complete=False)

        response = await client.post(
            f"{API}/admin/users/{other_admin.id}/suspend",
            headers=await auth(admin),
            json={"days": 1, "reason": "nope"},
        )
        assert response.status_code == 400

    async def test_reinstating_restores_access(
        self, client: AsyncClient, users, auth
    ):
        admin = await users.create(is_admin=True, with_photo=False, complete=False)
        offender = await users.create()
        admin_headers = await auth(admin)

        await client.post(
            f"{API}/admin/users/{offender.id}/suspend",
            headers=admin_headers,
            json={"days": 30, "reason": "test"},
        )
        await client.post(
            f"{API}/admin/users/{offender.id}/reinstate", headers=admin_headers
        )

        relogin = await client.post(
            f"{API}/auth/login",
            json={"email": offender.email, "password": "TestPass123!"},
        )
        token = relogin.json()["access_token"]
        response = await client.get(
            f"{API}/discovery", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200


class TestPhotoModeration:
    async def test_pending_photo_is_invisible_until_approved(
        self, client: AsyncClient, users, auth, session_factory
    ):
        admin = await users.create(is_admin=True, with_photo=False, complete=False)
        viewer = await users.create()
        owner = await users.create(
            display_name="Owner", photo_approved=False, complete=False
        )

        # Not discoverable while the only photo is pending.
        feed = await client.get(f"{API}/discovery", headers=await auth(viewer))
        assert "Owner" not in {p["display_name"] for p in feed.json()["profiles"]}

        async with session_factory() as db:
            photo_id = (
                await db.execute(
                    select(ProfilePhoto.id)
                    .join(Profile, Profile.id == ProfilePhoto.profile_id)
                    .where(Profile.user_id == owner.id)
                )
            ).scalar_one()

        approved = await client.post(
            f"{API}/admin/photos/{photo_id}/moderate",
            headers=await auth(admin),
            json={"status": "approved"},
        )
        assert approved.status_code == 200

        feed = await client.get(f"{API}/discovery", headers=await auth(viewer))
        assert "Owner" in {p["display_name"] for p in feed.json()["profiles"]}

    async def test_rejecting_a_photo_notifies_the_owner(
        self, client: AsyncClient, users, auth, session_factory
    ):
        from app.models import Notification, NotificationType

        admin = await users.create(is_admin=True, with_photo=False, complete=False)
        owner = await users.create(photo_approved=False, complete=False)

        async with session_factory() as db:
            photo_id = (
                await db.execute(
                    select(ProfilePhoto.id)
                    .join(Profile, Profile.id == ProfilePhoto.profile_id)
                    .where(Profile.user_id == owner.id)
                )
            ).scalar_one()

        await client.post(
            f"{API}/admin/photos/{photo_id}/moderate",
            headers=await auth(admin),
            json={"status": "rejected", "note": "Please upload a clear photo"},
        )

        async with session_factory() as db:
            note = (
                await db.execute(
                    select(Notification).where(
                        Notification.type == NotificationType.PHOTO_REJECTED
                    )
                )
            ).scalar_one()
            assert note.user_id == owner.id
            assert "clear photo" in note.body

    async def test_moderation_queue_lists_pending_photos(
        self, client: AsyncClient, users, auth
    ):
        admin = await users.create(is_admin=True, with_photo=False, complete=False)
        await users.create(display_name="Waiting", photo_approved=False, complete=False)

        queue = await client.get(f"{API}/admin/photos", headers=await auth(admin))
        assert queue.status_code == 200
        assert [row["display_name"] for row in queue.json()] == ["Waiting"]

    async def test_moderation_decision_must_be_a_decision(
        self, client: AsyncClient, users, auth, session_factory
    ):
        admin = await users.create(is_admin=True, with_photo=False, complete=False)
        owner = await users.create(photo_approved=False, complete=False)
        async with session_factory() as db:
            photo_id = (
                await db.execute(
                    select(ProfilePhoto.id)
                    .join(Profile, Profile.id == ProfilePhoto.profile_id)
                    .where(Profile.user_id == owner.id)
                )
            ).scalar_one()

        response = await client.post(
            f"{API}/admin/photos/{photo_id}/moderate",
            headers=await auth(admin),
            json={"status": "pending"},
        )
        assert response.status_code == 422


class TestAccountDeletion:
    async def test_deletion_scrubs_personal_data(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create(email="goodbye@amora.dev")
        b = await users.create()
        conversation = await make_match(client, auth, a, b)
        await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(a),
            json={"body": "see you"},
        )

        response = await client.request(
            "DELETE",
            f"{API}/auth/me",
            headers=await auth(a),
            json={"password": "TestPass123!", "confirmation": "DELETE"},
        )
        assert response.status_code == 200

        async with session_factory() as db:
            user = (
                await db.execute(select(User).where(User.id == a.id))
            ).scalar_one()
            # Tombstone retained, identity gone.
            assert user.status == UserStatus.DELETED
            assert user.deleted_at is not None
            assert user.email != "goodbye@amora.dev"
            assert "goodbye" not in user.email

            # Profile, photos and messages are gone.
            assert (
                await db.execute(
                    select(func.count(Profile.id)).where(Profile.user_id == a.id)
                )
            ).scalar_one() == 0
            assert (
                await db.execute(
                    select(func.count(Message.id)).where(Message.sender_id == a.id)
                )
            ).scalar_one() == 0

    async def test_deleted_user_cannot_log_in_or_use_tokens(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        headers = await auth(a)

        await client.request(
            "DELETE",
            f"{API}/auth/me",
            headers=headers,
            json={"password": "TestPass123!", "confirmation": "DELETE"},
        )

        assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 401
        relogin = await client.post(
            f"{API}/auth/login",
            json={"email": a.email, "password": "TestPass123!"},
        )
        assert relogin.status_code == 401

    async def test_deletion_requires_the_correct_password(
        self, client: AsyncClient, users, auth
    ):
        """A stolen token alone must not be able to destroy an account."""
        a = await users.create()
        response = await client.request(
            "DELETE",
            f"{API}/auth/me",
            headers=await auth(a),
            json={"password": "WrongPass123!", "confirmation": "DELETE"},
        )
        assert response.status_code == 400

    async def test_deletion_requires_typed_confirmation(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        response = await client.request(
            "DELETE",
            f"{API}/auth/me",
            headers=await auth(a),
            json={"password": "TestPass123!", "confirmation": "yes"},
        )
        assert response.status_code == 422

    async def test_the_other_party_no_longer_sees_the_match(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        await make_match(client, auth, a, b)

        await client.request(
            "DELETE",
            f"{API}/auth/me",
            headers=await auth(a),
            json={"password": "TestPass123!", "confirmation": "DELETE"},
        )

        matches = await client.get(f"{API}/matches", headers=await auth(b))
        assert matches.json()["matches"] == []
        conversations = await client.get(f"{API}/conversations", headers=await auth(b))
        assert conversations.json()["conversations"] == []

    async def test_reports_against_a_deleted_user_survive(
        self, client: AsyncClient, users, auth, session_factory
    ):
        """A bad actor must not erase the moderation trail by deleting."""
        offender = await users.create()
        reporter = await users.create()

        await client.post(
            f"{API}/safety/report",
            headers=await auth(reporter),
            json={"user_id": str(offender.id), "reason": "scam"},
        )

        await client.request(
            "DELETE",
            f"{API}/auth/me",
            headers=await auth(offender),
            json={"password": "TestPass123!", "confirmation": "DELETE"},
        )

        async with session_factory() as db:
            remaining = (
                await db.execute(select(func.count(Report.id)))
            ).scalar_one()
        assert remaining == 1


class TestProfileAndPrivacySettings:
    async def test_profile_update_and_interests(
        self, client: AsyncClient, users, auth, interests
    ):
        a = await users.create()
        headers = await auth(a)

        response = await client.patch(
            f"{API}/profiles/me",
            headers=headers,
            json={
                "display_name": "Updated Name",
                "bio": "A new bio",
                "job_title": "Potter",
                "height_cm": 170,
                "interests": ["hiking", "jazz"],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["display_name"] == "Updated Name"
        assert body["height_cm"] == 170
        assert {i["slug"] for i in body["interests"]} == {"hiking", "jazz"}

    async def test_unknown_interest_is_rejected(
        self, client: AsyncClient, users, auth, interests
    ):
        a = await users.create()
        response = await client.patch(
            f"{API}/profiles/me",
            headers=await auth(a),
            json={"interests": ["hiking", "not-a-real-interest"]},
        )
        assert response.status_code == 400

    async def test_my_profile_never_exposes_coordinates(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create(latitude=12.98, longitude=77.61)
        response = await client.get(f"{API}/profiles/me", headers=await auth(a))
        raw = response.text
        assert "latitude" not in raw
        assert "12.98" not in raw

    async def test_turning_off_discovery_hides_the_profile(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create(display_name="GoingDark")
        viewer = await users.create()

        before = await client.get(f"{API}/discovery", headers=await auth(viewer))
        assert "GoingDark" in {p["display_name"] for p in before.json()["profiles"]}

        await client.patch(
            f"{API}/profiles/me",
            headers=await auth(a),
            json={"is_discoverable": False},
        )

        after = await client.get(f"{API}/discovery", headers=await auth(viewer))
        assert "GoingDark" not in {p["display_name"] for p in after.json()["profiles"]}

    async def test_preferences_reject_inverted_age_range(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        response = await client.patch(
            f"{API}/preferences",
            headers=await auth(a),
            json={"min_age": 40, "max_age": 25},
        )
        assert response.status_code == 400

    async def test_preferences_cannot_go_below_18(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        response = await client.patch(
            f"{API}/preferences", headers=await auth(a), json={"min_age": 16}
        )
        assert response.status_code == 422

    async def test_location_update_returns_only_coarse_data(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        response = await client.put(
            f"{API}/profiles/me/location",
            headers=await auth(a),
            json={
                "latitude": 12.9000,
                "longitude": 77.6000,
                "city": "Bengaluru",
                "country": "in",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["city"] == "Bengaluru"
        assert body["country"] == "IN"
        assert "latitude" not in body

    async def test_photo_upload_ticket_is_presigned(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create(with_photo=False, complete=False)
        response = await client.post(
            f"{API}/profiles/me/photos",
            headers=await auth(a),
            json={"content_type": "image/jpeg", "byte_size": 20000},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["method"] == "PUT"
        assert "signature" in body["upload_url"] or "sig" in body["upload_url"]
        assert body["headers"]["Content-Type"] == "image/jpeg"

    async def test_photo_limit_is_enforced(self, client: AsyncClient, users, auth):
        a = await users.create(with_photo=False, complete=False)
        headers = await auth(a)

        for _ in range(settings.max_photos_per_user):
            response = await client.post(
                f"{API}/profiles/me/photos",
                headers=headers,
                json={"content_type": "image/jpeg", "byte_size": 1000},
            )
            assert response.status_code == 201

        over = await client.post(
            f"{API}/profiles/me/photos",
            headers=headers,
            json={"content_type": "image/jpeg", "byte_size": 1000},
        )
        assert over.status_code == 409

    async def test_rejects_non_image_content_type(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        response = await client.post(
            f"{API}/profiles/me/photos",
            headers=await auth(a),
            json={"content_type": "application/pdf", "byte_size": 1000},
        )
        assert response.status_code == 422

    async def test_cannot_delete_someone_elses_photo(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()

        async with session_factory() as db:
            profile_b = (
                await db.execute(select(Profile).where(Profile.user_id == b.id))
            ).scalar_one()
            photo_b = (
                await db.execute(
                    select(ProfilePhoto).where(
                        ProfilePhoto.profile_id == profile_b.id
                    )
                )
            ).scalar_one()
            photo_id = photo_b.id

        response = await client.delete(
            f"{API}/profiles/me/photos/{photo_id}", headers=await auth(a)
        )
        assert response.status_code == 404
