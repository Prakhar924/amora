"""End-to-end happy path.

Walks the entire product in one test, through the public HTTP API only, with
no direct database writes except the two steps that are genuinely out of band
(clicking a link in an email, and an admin approving a photo).

This is the "Definition of Done" expressed as a test: register, verify, build
a profile, upload a photo, get it approved, discover each other, like, match,
chat in both directions, read receipts, block, report, and delete.
"""

from __future__ import annotations

from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_email_token
from app.models import ProfilePhoto, User

API = settings.api_prefix
PASSWORD = "JourneyPass123"


def _dob(age: int) -> str:
    return (date.today() - timedelta(days=age * 365 + 30)).isoformat()


class TestFullUserJourney:
    async def test_two_strangers_register_match_and_chat(
        self, client: AsyncClient, users, auth, session_factory, interests
    ):
        # ------------------------------------------------------------------
        # 1. Two adults register
        # ------------------------------------------------------------------
        people = {
            "rose": {
                "email": "rose@amora.dev",
                "display_name": "Rose",
                "gender": "woman",
                "age": 29,
            },
            "kai": {
                "email": "kai@amora.dev",
                "display_name": "Kai",
                "gender": "man",
                "age": 31,
            },
        }

        for person in people.values():
            response = await client.post(
                f"{API}/auth/register",
                json={
                    "email": person["email"],
                    "password": PASSWORD,
                    "display_name": person["display_name"],
                    "date_of_birth": _dob(person["age"]),
                    "gender": person["gender"],
                    "accept_terms": True,
                    "accept_privacy": True,
                },
            )
            assert response.status_code == 202, response.text

        # ------------------------------------------------------------------
        # 2. They cannot sign in until the email is confirmed
        # ------------------------------------------------------------------
        premature = await client.post(
            f"{API}/auth/login",
            json={"email": people["rose"]["email"], "password": PASSWORD},
        )
        assert premature.status_code == 403

        # Clicking the emailed link is the only out-of-band step; the token is
        # minted exactly as the email would contain it.
        async with session_factory() as db:
            for person in people.values():
                user = (
                    await db.execute(
                        select(User).where(User.email == person["email"])
                    )
                ).scalar_one()
                person["id"] = str(user.id)

        for person in people.values():
            verified = await client.post(
                f"{API}/auth/verify-email",
                json={
                    "token": create_email_token(
                        __import__("uuid").UUID(person["id"]), "verify_email"
                    )
                },
            )
            assert verified.status_code == 200

        # ------------------------------------------------------------------
        # 3. Sign in and receive a token pair
        # ------------------------------------------------------------------
        for person in people.values():
            login = await client.post(
                f"{API}/auth/login",
                json={"email": person["email"], "password": PASSWORD},
            )
            assert login.status_code == 200
            tokens = login.json()
            person["headers"] = {
                "Authorization": f"Bearer {tokens['access_token']}"
            }
            person["refresh"] = tokens["refresh_token"]

        rose, kai = people["rose"], people["kai"]

        # Age is derived from date of birth, never trusted from the client.
        me = await client.get(f"{API}/auth/me", headers=rose["headers"])
        assert me.json()["age"] == 29
        assert me.json()["profile_complete"] is False

        # ------------------------------------------------------------------
        # 4. Cannot enter the dating loop with an unfinished profile
        # ------------------------------------------------------------------
        gated = await client.get(f"{API}/discovery", headers=rose["headers"])
        assert gated.status_code == 403

        # ------------------------------------------------------------------
        # 5. Build out profiles: bio, interests, location, preferences
        # ------------------------------------------------------------------
        for person in (rose, kai):
            updated = await client.patch(
                f"{API}/profiles/me",
                headers=person["headers"],
                json={
                    "bio": f"{person['display_name']} likes long walks.",
                    "job_title": "Potter",
                    "height_cm": 172,
                    "interests": ["hiking", "coffee"],
                },
            )
            assert updated.status_code == 200
            assert {i["slug"] for i in updated.json()["interests"]} == {
                "hiking",
                "coffee",
            }

            located = await client.put(
                f"{API}/profiles/me/location",
                headers=person["headers"],
                json={
                    "latitude": 12.9716,
                    "longitude": 77.5946,
                    "city": "Bengaluru",
                    "country": "IN",
                },
            )
            assert located.status_code == 200

            prefs = await client.patch(
                f"{API}/preferences",
                headers=person["headers"],
                json={
                    "min_age": 25,
                    "max_age": 40,
                    "max_distance_km": 100,
                    "interested_in": [],
                },
            )
            assert prefs.status_code == 200

        # ------------------------------------------------------------------
        # 6. Upload a photo via a presigned ticket, then confirm it
        # ------------------------------------------------------------------
        for person in (rose, kai):
            ticket = await client.post(
                f"{API}/profiles/me/photos",
                headers=person["headers"],
                json={"content_type": "image/jpeg", "byte_size": 42000},
            )
            assert ticket.status_code == 201
            body = ticket.json()
            # The client uploads straight to object storage, not through us.
            assert body["method"] == "PUT"
            assert body["upload_url"].startswith("http")

            confirmed = await client.post(
                f"{API}/profiles/me/photos/{body['photo_id']}/confirm",
                headers=person["headers"],
            )
            assert confirmed.status_code == 200
            photo = confirmed.json()["photos"][0]
            assert photo["upload_confirmed"] is True
            # Moderation is required, so it is not yet public.
            assert photo["moderation_status"] == "pending"

        # Still not discoverable: the photo awaits review.
        assert (
            await client.get(f"{API}/discovery", headers=rose["headers"])
        ).status_code == 403

        # ------------------------------------------------------------------
        # 7. An admin approves both photos
        # ------------------------------------------------------------------
        admin = await users.create(is_admin=True, email="moderator@amora.dev")
        admin_headers = await auth(admin)

        queue = await client.get(f"{API}/admin/photos", headers=admin_headers)
        assert queue.status_code == 200
        pending = {row["display_name"]: row["id"] for row in queue.json()}
        assert {"Rose", "Kai"} <= set(pending)

        for photo_id in pending.values():
            approved = await client.post(
                f"{API}/admin/photos/{photo_id}/moderate",
                headers=admin_headers,
                json={"status": "approved"},
            )
            assert approved.status_code == 200

        # ------------------------------------------------------------------
        # 8. Discovery now works and each can see the other
        # ------------------------------------------------------------------
        feed = await client.get(f"{API}/discovery", headers=rose["headers"])
        assert feed.status_code == 200
        candidates = feed.json()["profiles"]
        kai_card = next(
            (c for c in candidates if c["display_name"] == "Kai"), None
        )
        assert kai_card is not None, "Kai did not appear in Rose's feed"
        assert kai_card["age"] == 31
        assert kai_card["distance_km"] is not None
        assert len(kai_card["photos"]) == 1
        # Privacy: no coordinates anywhere in the card.
        assert "latitude" not in feed.text

        # ------------------------------------------------------------------
        # 9. Rose likes Kai — no match yet
        # ------------------------------------------------------------------
        first_swipe = await client.post(
            f"{API}/discovery/swipe",
            headers=rose["headers"],
            json={"target_user_id": kai["id"], "action": "like"},
        )
        assert first_swipe.status_code == 200
        assert first_swipe.json()["is_match"] is False

        # Kai is now excluded from Rose's feed: no duplicate recommendations.
        refetched = await client.get(f"{API}/discovery", headers=rose["headers"])
        assert "Kai" not in {
            c["display_name"] for c in refetched.json()["profiles"]
        }

        # ------------------------------------------------------------------
        # 10. Kai likes back — it's a match
        # ------------------------------------------------------------------
        second_swipe = await client.post(
            f"{API}/discovery/swipe",
            headers=kai["headers"],
            json={"target_user_id": rose["id"], "action": "like"},
        )
        assert second_swipe.status_code == 200
        match_body = second_swipe.json()
        assert match_body["is_match"] is True
        conversation_id = match_body["match"]["conversation_id"]
        assert conversation_id

        # Both see the match in their list.
        for person, expected in ((rose, "Kai"), (kai, "Rose")):
            listing = await client.get(f"{API}/matches", headers=person["headers"])
            matches = listing.json()["matches"]
            assert len(matches) == 1
            assert matches[0]["profile"]["display_name"] == expected

        # Both were notified.
        for person in (rose, kai):
            notifications = await client.get(
                f"{API}/notifications", headers=person["headers"]
            )
            kinds = {n["type"] for n in notifications.json()["notifications"]}
            assert "new_match" in kinds

        # ------------------------------------------------------------------
        # 11. They chat, in both directions
        # ------------------------------------------------------------------
        sent = await client.post(
            f"{API}/conversations/{conversation_id}/messages",
            headers=rose["headers"],
            json={"body": "Hi Kai! Those mugs are lovely.", "client_nonce": "r1"},
        )
        assert sent.status_code == 201

        replied = await client.post(
            f"{API}/conversations/{conversation_id}/messages",
            headers=kai["headers"],
            json={"body": "Thanks Rose! Coffee on Saturday?", "client_nonce": "k1"},
        )
        assert replied.status_code == 201

        history = await client.get(
            f"{API}/conversations/{conversation_id}/messages",
            headers=rose["headers"],
        )
        bodies = [m["body"] for m in history.json()["messages"]]
        assert bodies == [
            "Thanks Rose! Coffee on Saturday?",
            "Hi Kai! Those mugs are lovely.",
        ]

        # Unread counters and read receipts
        chat_list = await client.get(
            f"{API}/conversations", headers=rose["headers"]
        )
        assert chat_list.json()["total_unread"] == 1

        read = await client.post(
            f"{API}/conversations/{conversation_id}/read",
            headers=rose["headers"],
            json={},
        )
        assert read.json()["unread_count"] == 0

        # ------------------------------------------------------------------
        # 12. Token refresh keeps the session alive
        # ------------------------------------------------------------------
        refreshed = await client.post(
            f"{API}/auth/refresh", json={"refresh_token": rose["refresh"]}
        )
        assert refreshed.status_code == 200
        rose["headers"] = {
            "Authorization": f"Bearer {refreshed.json()['access_token']}"
        }
        assert (
            await client.get(f"{API}/auth/me", headers=rose["headers"])
        ).status_code == 200

        # ------------------------------------------------------------------
        # 13. Safety: Rose reports and blocks Kai
        # ------------------------------------------------------------------
        reported = await client.post(
            f"{API}/safety/report",
            headers=rose["headers"],
            json={
                "user_id": kai["id"],
                "reason": "harassment",
                "details": "Inappropriate messages",
                "conversation_id": conversation_id,
            },
        )
        assert reported.status_code == 201

        blocked = await client.post(
            f"{API}/safety/block",
            headers=rose["headers"],
            json={"user_id": kai["id"]},
        )
        assert blocked.status_code == 201

        # The chat is now closed for both of them.
        for person in (rose, kai):
            denied = await client.post(
                f"{API}/conversations/{conversation_id}/messages",
                headers=person["headers"],
                json={"body": "are you there?"},
            )
            assert denied.status_code == 403

        # And the match is gone.
        assert (
            await client.get(f"{API}/matches", headers=kai["headers"])
        ).json()["matches"] == []

        # The moderator sees the report with context.
        admin_queue = await client.get(
            f"{API}/admin/reports", headers=admin_headers
        )
        rows = admin_queue.json()
        assert len(rows) == 1
        assert rows[0]["reported_display_name"] == "Kai"

        resolved = await client.post(
            f"{API}/admin/reports/{rows[0]['id']}/resolve",
            headers=admin_headers,
            json={
                "status": "actioned",
                "resolution_note": "Warned the user",
                "suspend_days": 3,
            },
        )
        assert resolved.status_code == 200

        # ------------------------------------------------------------------
        # 14. Rose deletes her account
        # ------------------------------------------------------------------
        deleted = await client.request(
            "DELETE",
            f"{API}/auth/me",
            headers=rose["headers"],
            json={"password": PASSWORD, "confirmation": "DELETE"},
        )
        assert deleted.status_code == 200

        assert (
            await client.get(f"{API}/auth/me", headers=rose["headers"])
        ).status_code == 401
        assert (
            await client.post(
                f"{API}/auth/login",
                json={"email": "rose@amora.dev", "password": PASSWORD},
            )
        ).status_code == 401

        # Her personal data is gone, while the report against Kai survives.
        async with session_factory() as db:
            tombstone = (
                await db.execute(select(User).where(User.id == rose["id"]))
            ).scalar_one()
            assert tombstone.deleted_at is not None
            assert tombstone.email != "rose@amora.dev"
            assert (
                await db.execute(select(ProfilePhoto))
            ).scalars().first() is not None  # Kai's photo remains
