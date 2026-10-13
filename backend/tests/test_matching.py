"""Mutual matching, duplicate prevention, and concurrent likes."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.models import Conversation, Like, LikeAction, Match, MessageRead
from app.services.matching import register_like

API = settings.api_prefix


class TestMutualMatching:
    async def test_one_sided_like_creates_no_match(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()
        headers = await auth(a)

        response = await client.post(
            f"{API}/discovery/swipe",
            headers=headers,
            json={"target_user_id": str(b.id), "action": "like"},
        )
        assert response.status_code == 200
        assert response.json()["is_match"] is False

        async with session_factory() as db:
            assert (await db.execute(select(func.count(Match.id)))).scalar_one() == 0

    async def test_reciprocated_like_creates_a_match(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()

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
        assert body["is_match"] is True
        assert body["match"]["conversation_id"] is not None
        assert body["match"]["profile"]["user_id"] == str(a.id)

        async with session_factory() as db:
            match = (await db.execute(select(Match))).scalar_one()
            # The pair must be stored in canonical order for the UNIQUE
            # constraint to prevent duplicates.
            assert str(match.user_a_id) < str(match.user_b_id)
            assert {match.user_a_id, match.user_b_id} == {a.id, b.id}

            # A conversation and both read-state rows come with the match.
            conversation = (
                await db.execute(
                    select(Conversation).where(Conversation.match_id == match.id)
                )
            ).scalar_one()
            reads = (
                (
                    await db.execute(
                        select(MessageRead).where(
                            MessageRead.conversation_id == conversation.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(reads) == 2

    async def test_pass_then_like_does_not_match(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()

        await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(a),
            json={"target_user_id": str(b.id), "action": "pass"},
        )
        response = await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(b),
            json={"target_user_id": str(a.id), "action": "like"},
        )
        assert response.json()["is_match"] is False

    async def test_superlike_counts_as_interest(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()

        await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(a),
            json={"target_user_id": str(b.id), "action": "superlike"},
        )
        response = await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(b),
            json={"target_user_id": str(a.id), "action": "like"},
        )
        assert response.json()["is_match"] is True

    async def test_cannot_swipe_on_self(self, client: AsyncClient, users, auth):
        a = await users.create()
        response = await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(a),
            json={"target_user_id": str(a.id), "action": "like"},
        )
        assert response.status_code == 400

    async def test_both_users_are_notified(
        self, client: AsyncClient, users, auth, session_factory
    ):
        from app.models import Notification, NotificationType

        a = await users.create()
        b = await users.create()
        await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(a),
            json={"target_user_id": str(b.id), "action": "like"},
        )
        await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(b),
            json={"target_user_id": str(a.id), "action": "like"},
        )

        async with session_factory() as db:
            notified = (
                (
                    await db.execute(
                        select(Notification.user_id).where(
                            Notification.type == NotificationType.NEW_MATCH
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert set(notified) == {a.id, b.id}, "both sides must be notified"

    async def test_like_notification_does_not_name_the_liker(
        self, client: AsyncClient, users, auth, session_factory
    ):
        """A one-sided like must not reveal who it came from."""
        from app.models import Notification, NotificationType

        a = await users.create(display_name="Distinctive")
        b = await users.create()
        await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(a),
            json={"target_user_id": str(b.id), "action": "like"},
        )

        async with session_factory() as db:
            note = (
                await db.execute(
                    select(Notification).where(
                        Notification.type == NotificationType.NEW_LIKE
                    )
                )
            ).scalar_one()
        assert note.user_id == b.id
        blob = f"{note.title} {note.body} {note.payload}"
        assert "Distinctive" not in blob
        assert str(a.id) not in blob


class TestDuplicatePrevention:
    async def test_repeated_mutual_likes_create_only_one_match(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()
        a_headers = await auth(a)
        b_headers = await auth(b)

        for _ in range(4):
            await client.post(
                f"{API}/discovery/swipe",
                headers=a_headers,
                json={"target_user_id": str(b.id), "action": "like"},
            )
            await client.post(
                f"{API}/discovery/swipe",
                headers=b_headers,
                json={"target_user_id": str(a.id), "action": "like"},
            )

        async with session_factory() as db:
            assert (await db.execute(select(func.count(Match.id)))).scalar_one() == 1
            # One decision row per direction, updated rather than duplicated.
            assert (await db.execute(select(func.count(Like.id)))).scalar_one() == 2

    async def test_match_reported_only_once(self, client: AsyncClient, users, auth):
        """`is_match` must be true exactly once, so the UI celebrates once."""
        a = await users.create()
        b = await users.create()
        a_headers = await auth(a)
        b_headers = await auth(b)

        await client.post(
            f"{API}/discovery/swipe",
            headers=a_headers,
            json={"target_user_id": str(b.id), "action": "like"},
        )
        first = await client.post(
            f"{API}/discovery/swipe",
            headers=b_headers,
            json={"target_user_id": str(a.id), "action": "like"},
        )
        second = await client.post(
            f"{API}/discovery/swipe",
            headers=b_headers,
            json={"target_user_id": str(a.id), "action": "like"},
        )
        assert first.json()["is_match"] is True
        assert second.json()["is_match"] is False
        assert second.json()["was_update"] is True

    async def test_re_swipe_updates_the_existing_decision(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()
        headers = await auth(a)

        await client.post(
            f"{API}/discovery/swipe",
            headers=headers,
            json={"target_user_id": str(b.id), "action": "pass"},
        )
        await client.post(
            f"{API}/discovery/swipe",
            headers=headers,
            json={"target_user_id": str(b.id), "action": "like"},
        )

        async with session_factory() as db:
            like = (await db.execute(select(Like))).scalar_one()
            assert like.action == LikeAction.LIKE


class TestConcurrentLikes:
    """The race the schema is designed for: simultaneous mutual likes."""

    @pytest.mark.slow
    async def test_simultaneous_mutual_likes_create_exactly_one_match(
        self, engine, users, session_factory
    ):
        a = await users.create()
        b = await users.create()

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async def like(actor_id, target_id):
            # Each side runs in its own session/transaction, as two API
            # workers handling simultaneous requests would.
            async with factory() as db:
                try:
                    await register_like(db, actor_id, target_id, LikeAction.LIKE)
                    await db.commit()
                    return None
                except Exception as exc:
                    await db.rollback()
                    return exc

        results = await asyncio.gather(
            like(a.id, b.id), like(b.id, a.id), return_exceptions=True
        )

        async with session_factory() as db:
            count = (await db.execute(select(func.count(Match.id)))).scalar_one()
            conversations = (
                await db.execute(select(func.count(Conversation.id)))
            ).scalar_one()

        assert count == 1, (
            f"expected exactly one match from a simultaneous mutual like, "
            f"got {count}. Task results: {results}"
        )
        assert conversations <= 1, "a duplicate conversation was created"

    @pytest.mark.slow
    async def test_many_concurrent_likes_on_one_target(
        self, engine, users, session_factory
    ):
        """Ten admirers liking the same person must not corrupt anything."""
        target = await users.create()
        admirers = [await users.create() for _ in range(10)]

        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async def like(actor_id):
            async with factory() as db:
                await register_like(db, actor_id, target.id, LikeAction.LIKE)
                await db.commit()

        await asyncio.gather(*(like(u.id) for u in admirers))

        async with session_factory() as db:
            likes = (await db.execute(select(func.count(Like.id)))).scalar_one()
            matches = (await db.execute(select(func.count(Match.id)))).scalar_one()

        assert likes == 10
        assert matches == 0, "no match should exist without reciprocation"

    @pytest.mark.slow
    async def test_repeated_concurrent_likes_are_idempotent(
        self, engine, users, session_factory
    ):
        """The same like sent eight times at once must leave one row."""
        a = await users.create()
        b = await users.create()
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)

        async def like():
            async with factory() as db:
                try:
                    await register_like(db, a.id, b.id, LikeAction.LIKE)
                    await db.commit()
                except Exception:
                    await db.rollback()

        await asyncio.gather(*(like() for _ in range(8)))

        async with session_factory() as db:
            assert (await db.execute(select(func.count(Like.id)))).scalar_one() == 1


class TestUnmatch:
    async def test_unmatch_ends_the_match_and_retracts_the_like(
        self, client: AsyncClient, users, auth, session_factory
    ):
        a = await users.create()
        b = await users.create()
        a_headers = await auth(a)

        await client.post(
            f"{API}/discovery/swipe",
            headers=a_headers,
            json={"target_user_id": str(b.id), "action": "like"},
        )
        matched = await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(b),
            json={"target_user_id": str(a.id), "action": "like"},
        )
        match_id = matched.json()["match"]["id"]

        response = await client.delete(f"{API}/matches/{match_id}", headers=a_headers)
        assert response.status_code == 200

        async with session_factory() as db:
            match = (await db.execute(select(Match))).scalar_one()
            assert match.unmatched_at is not None
            assert match.unmatched_by_id == a.id
            # The unmatcher's like becomes a pass, so the other person
            # re-liking cannot silently restore the match.
            like = (
                await db.execute(
                    select(Like).where(Like.actor_id == a.id, Like.target_id == b.id)
                )
            ).scalar_one()
            assert like.action == LikeAction.PASS

        listing = await client.get(f"{API}/matches", headers=a_headers)
        assert listing.json()["matches"] == []

    async def test_cannot_unmatch_someone_elses_match(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create()
        b = await users.create()
        outsider = await users.create()

        await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(a),
            json={"target_user_id": str(b.id), "action": "like"},
        )
        matched = await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(b),
            json={"target_user_id": str(a.id), "action": "like"},
        )
        match_id = matched.json()["match"]["id"]

        response = await client.delete(
            f"{API}/matches/{match_id}", headers=await auth(outsider)
        )
        assert response.status_code == 404

    async def test_match_list_shows_the_other_person(
        self, client: AsyncClient, users, auth
    ):
        a = await users.create(display_name="Alice")
        b = await users.create(display_name="Blake")

        await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(a),
            json={"target_user_id": str(b.id), "action": "like"},
        )
        await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(b),
            json={"target_user_id": str(a.id), "action": "like"},
        )

        listing = await client.get(f"{API}/matches", headers=await auth(a))
        matches = listing.json()["matches"]
        assert len(matches) == 1
        assert matches[0]["profile"]["display_name"] == "Blake"
