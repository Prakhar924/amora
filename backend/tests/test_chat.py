"""Chat authorization and messaging behaviour.

The central rule under test: only mutually matched, unblocked users can
exchange messages, and it is enforced by the backend rather than the client.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.config import settings
from app.models import Message

API = settings.api_prefix


async def make_match(client: AsyncClient, auth, a, b) -> str:
    """Mutually like two users and return their conversation id."""
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
    body = response.json()
    assert body["is_match"] is True, body
    return body["match"]["conversation_id"]


class TestChatPermissions:
    async def test_matched_users_can_exchange_messages(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)

        sent = await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(a),
            json={"body": "Hello there"},
        )
        assert sent.status_code == 201
        assert sent.json()["body"] == "Hello there"

        history = await client.get(
            f"{API}/conversations/{conversation}/messages", headers=await auth(b)
        )
        assert [m["body"] for m in history.json()["messages"]] == ["Hello there"]

    async def test_unmatched_users_have_no_conversation(
        self, client: AsyncClient, users, auth
    ):
        """Without a match there is no thread to address at all."""
        a = await users.create()
        # A second user exists but was never swiped on, so no match links them.
        await users.create()

        conversations = await client.get(f"{API}/conversations", headers=await auth(a))
        assert conversations.json()["conversations"] == []

    async def test_outsider_cannot_read_a_conversation(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        outsider = await users.create()
        conversation = await make_match(client, auth, a, b)

        response = await client.get(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(outsider),
        )
        # 404, not 403: the thread's existence is not confirmed.
        assert response.status_code == 404

    async def test_outsider_cannot_send_to_a_conversation(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()
        outsider = await users.create()
        conversation = await make_match(client, auth, a, b)

        response = await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(outsider),
            json={"body": "let me in"},
        )
        assert response.status_code == 404

        async with session_factory() as db:
            count = (await db.execute(select(func.count(Message.id)))).scalar_one()
        assert count == 0, "an outsider's message was persisted"

    async def test_cannot_send_after_unmatching(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)

        matches = await client.get(f"{API}/matches", headers=await auth(a))
        match_id = matches.json()["matches"][0]["id"]
        await client.delete(f"{API}/matches/{match_id}", headers=await auth(a))

        for user in (a, b):
            response = await client.post(
                f"{API}/conversations/{conversation}/messages",
                headers=await auth(user),
                json={"body": "still here?"},
            )
            assert response.status_code == 403

    async def test_cannot_send_after_being_blocked(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)

        await client.post(
            f"{API}/safety/block",
            headers=await auth(b),
            json={"user_id": str(a.id)},
        )

        response = await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(a),
            json={"body": "hello?"},
        )
        assert response.status_code == 403

    async def test_blocker_also_cannot_send(self, client: AsyncClient, users, auth):
        """A block is mutual: it silences both directions."""
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)

        await client.post(
            f"{API}/safety/block",
            headers=await auth(b),
            json={"user_id": str(a.id)},
        )
        response = await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(b),
            json={"body": "one last word"},
        )
        assert response.status_code == 403

    async def test_unauthenticated_cannot_read_messages(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)

        response = await client.get(f"{API}/conversations/{conversation}/messages")
        assert response.status_code == 401

    async def test_nonexistent_conversation_is_404(
        self, client: AsyncClient, users, auth
    ):
        import uuid

        a = await users.create()
        response = await client.get(
            f"{API}/conversations/{uuid.uuid4()}/messages", headers=await auth(a)
        )
        assert response.status_code == 404


class TestMessageValidation:
    async def test_empty_and_whitespace_bodies_are_rejected(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)

        for body in ("", "   ", "\n\t "):
            response = await client.post(
                f"{API}/conversations/{conversation}/messages",
                headers=await auth(a),
                json={"body": body},
            )
            assert response.status_code == 422, body

    async def test_overlong_body_is_rejected(self, client: AsyncClient, users, auth):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)

        response = await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(a),
            json={"body": "x" * 5000},
        )
        assert response.status_code == 422

    async def test_client_nonce_makes_sending_idempotent(
        self, client: AsyncClient, users, auth, session_factory
    ):
        """A retry after a dropped connection must not duplicate the message."""
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)
        headers = await auth(a)

        payload = {"body": "only once", "client_nonce": "abc-123"}
        first = await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=headers,
            json=payload,
        )
        second = await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=headers,
            json=payload,
        )

        assert first.json()["id"] == second.json()["id"]
        async with session_factory() as db:
            count = (await db.execute(select(func.count(Message.id)))).scalar_one()
        assert count == 1


class TestUnreadAndReadReceipts:
    async def test_recipient_accrues_unread_count(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)
        a_headers = await auth(a)

        for i in range(3):
            await client.post(
                f"{API}/conversations/{conversation}/messages",
                headers=a_headers,
                json={"body": f"message {i}"},
            )

        listing = await client.get(f"{API}/conversations", headers=await auth(b))
        body = listing.json()
        assert body["total_unread"] == 3
        assert body["conversations"][0]["unread_count"] == 3

        # The sender has nothing unread from themselves.
        own = await client.get(f"{API}/conversations", headers=a_headers)
        assert own.json()["total_unread"] == 0

    async def test_marking_read_clears_the_counter(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)

        await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=await auth(a),
            json={"body": "unread"},
        )

        b_headers = await auth(b)
        response = await client.post(
            f"{API}/conversations/{conversation}/read", headers=b_headers, json={}
        )
        assert response.status_code == 200
        assert response.json()["unread_count"] == 0

        total = await client.get(
            f"{API}/conversations/unread-count", headers=b_headers
        )
        assert total.json()["total_unread"] == 0

    async def test_outsider_cannot_mark_a_conversation_read(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        outsider = await users.create()
        conversation = await make_match(client, auth, a, b)

        response = await client.post(
            f"{API}/conversations/{conversation}/read",
            headers=await auth(outsider),
            json={},
        )
        assert response.status_code == 404

    async def test_conversation_preview_reflects_the_latest_message(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)
        headers = await auth(a)

        await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=headers,
            json={"body": "first"},
        )
        await client.post(
            f"{API}/conversations/{conversation}/messages",
            headers=headers,
            json={"body": "most recent"},
        )

        listing = await client.get(f"{API}/conversations", headers=await auth(b))
        assert listing.json()["conversations"][0]["last_message_preview"] == (
            "most recent"
        )


class TestHistoryPagination:
    async def test_history_is_newest_first_and_pages_backwards(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        conversation = await make_match(client, auth, a, b)
        headers = await auth(a)

        for i in range(7):
            await client.post(
                f"{API}/conversations/{conversation}/messages",
                headers=headers,
                json={"body": f"m{i}"},
            )

        page1 = (
            await client.get(
                f"{API}/conversations/{conversation}/messages",
                headers=headers,
                params={"limit": 3},
            )
        ).json()
        assert [m["body"] for m in page1["messages"]] == ["m6", "m5", "m4"]
        assert page1["next_cursor"]

        page2 = (
            await client.get(
                f"{API}/conversations/{conversation}/messages",
                headers=headers,
                params={"limit": 3, "cursor": page1["next_cursor"]},
            )
        ).json()
        assert [m["body"] for m in page2["messages"]] == ["m3", "m2", "m1"]
