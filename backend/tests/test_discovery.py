"""Discovery filters, exclusions, and the privacy of the feed payload."""

from __future__ import annotations

import json

from httpx import AsyncClient

from app.core.config import settings
from app.models import Gender, UserStatus

API = settings.api_prefix

# Bengaluru, matching the UserFactory default.
HERE = (12.9716, 77.5946)
# ~45km away.
NEARBY = (13.3400, 77.7000)
# Another continent.
FAR = (52.5200, 13.4050)


async def feed(client: AsyncClient, headers: dict, **params) -> list[dict]:
    response = await client.get(f"{API}/discovery", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()["profiles"]


def names(profiles: list[dict]) -> set[str]:
    return {p["display_name"] for p in profiles}


class TestFeedBasics:
    async def test_shows_other_eligible_users(self, client: AsyncClient, users, auth):
        me = await users.create(display_name="Me")
        await users.create(display_name="Other")

        profiles = await feed(client, await auth(me))
        assert names(profiles) == {"Other"}

    async def test_never_shows_the_viewer(self, client: AsyncClient, users, auth):
        me = await users.create(display_name="Me")
        await users.create(display_name="Other")

        profiles = await feed(client, await auth(me))
        assert "Me" not in names(profiles)

    async def test_requires_a_completed_profile_to_browse(
        self, client: AsyncClient, users, auth
    ):
        """An empty profile must not be able to browse others."""
        me = await users.create(with_photo=False, complete=False)
        await users.create()

        response = await client.get(f"{API}/discovery", headers=await auth(me))
        assert response.status_code == 403
        assert "profile" in response.json()["detail"].lower()

    async def test_requires_authentication(self, client: AsyncClient):
        response = await client.get(f"{API}/discovery")
        assert response.status_code == 401


class TestExclusions:
    async def test_excludes_users_without_an_approved_photo(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create(display_name="Me")
        await users.create(display_name="NoPhoto", with_photo=False, complete=False)
        await users.create(
            display_name="PendingPhoto", photo_approved=False, complete=False
        )
        await users.create(display_name="Approved")

        profiles = await feed(client, await auth(me))
        assert names(profiles) == {"Approved"}

    async def test_excludes_non_discoverable_profiles(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create()
        await users.create(display_name="Hidden", discoverable=False)
        await users.create(display_name="Visible")

        profiles = await feed(client, await auth(me))
        assert names(profiles) == {"Visible"}

    async def test_excludes_match_only_profiles(self, client: AsyncClient, users, auth):
        me = await users.create()
        await users.create(display_name="MatchesOnly", show_only_to_matches=True)
        await users.create(display_name="Public")

        profiles = await feed(client, await auth(me))
        assert names(profiles) == {"Public"}

    async def test_excludes_unverified_and_suspended_accounts(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create()
        await users.create(
            display_name="Unverified",
            verified=False,
            status=UserStatus.PENDING_VERIFICATION,
        )
        await users.create(display_name="Suspended", status=UserStatus.SUSPENDED)
        await users.create(display_name="Active")

        profiles = await feed(client, await auth(me))
        assert names(profiles) == {"Active"}

    async def test_never_repeats_an_already_swiped_profile(
        self, client: AsyncClient, users, auth
    ):
        """The duplicate-recommendation guarantee."""
        me = await users.create()
        liked = await users.create(display_name="Liked")
        passed = await users.create(display_name="Passed")
        await users.create(display_name="Fresh")
        headers = await auth(me)

        assert names(await feed(client, headers)) == {"Liked", "Passed", "Fresh"}

        for target, action in ((liked, "like"), (passed, "pass")):
            await client.post(
                f"{API}/discovery/swipe",
                headers=headers,
                json={"target_user_id": str(target.id), "action": action},
            )

        assert names(await feed(client, headers)) == {"Fresh"}

    async def test_excludes_users_who_passed_on_the_viewer(
        self, client: AsyncClient, users, auth
    ):
        """Showing them would burn a swipe that can never match."""
        me = await users.create()
        rejecter = await users.create(display_name="Rejecter")

        await client.post(
            f"{API}/discovery/swipe",
            headers=await auth(rejecter),
            json={"target_user_id": str(me.id), "action": "pass"},
        )

        assert names(await feed(client, await auth(me))) == set()

    async def test_excludes_blocked_users_in_both_directions(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create()
        blocked_by_me = await users.create(display_name="IBlockedThem")
        blocked_me = await users.create(display_name="TheyBlockedMe")
        await users.create(display_name="Fine")

        await client.post(
            f"{API}/safety/block",
            headers=await auth(me),
            json={"user_id": str(blocked_by_me.id)},
        )
        await client.post(
            f"{API}/safety/block",
            headers=await auth(blocked_me),
            json={"user_id": str(me.id)},
        )

        assert names(await feed(client, await auth(me))) == {"Fine"}


class TestFilters:
    async def test_age_filter_is_respected(self, client: AsyncClient, users, auth):
        me = await users.create(age=30, min_age=25, max_age=35)
        await users.create(display_name="TooYoung", age=19)
        await users.create(display_name="InRange", age=28)
        await users.create(display_name="TooOld", age=55)

        assert names(await feed(client, await auth(me))) == {"InRange"}

    async def test_never_shows_a_minor_even_if_filters_allow_it(
        self, client: AsyncClient, users, auth, session_factory
    ):
        """The 18+ floor is independent of user preferences.

        The minor is inserted directly, bypassing registration validation, to
        prove the discovery query itself enforces the floor.
        """
        from sqlalchemy import update

        from app.models import User

        me = await users.create(age=30, min_age=18, max_age=99)
        minor = await users.create(display_name="Minor", age=25)

        async with session_factory() as db:
            from datetime import UTC, datetime, timedelta

            await db.execute(
                update(User)
                .where(User.id == minor.id)
                .values(date_of_birth=datetime.now(UTC) - timedelta(days=16 * 365))
            )
            await db.commit()

        assert "Minor" not in names(await feed(client, await auth(me)))

    async def test_gender_preference_is_respected(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create(interested_in=["woman"])
        await users.create(display_name="Woman", gender=Gender.WOMAN)
        await users.create(display_name="Man", gender=Gender.MAN)
        await users.create(display_name="Nonbinary", gender=Gender.NONBINARY)

        assert names(await feed(client, await auth(me))) == {"Woman"}

    async def test_empty_gender_preference_shows_everyone(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create(interested_in=[])
        await users.create(display_name="Woman", gender=Gender.WOMAN)
        await users.create(display_name="Man", gender=Gender.MAN)

        assert names(await feed(client, await auth(me))) == {"Woman", "Man"}

    async def test_distance_filter_excludes_far_users(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create(
            latitude=HERE[0], longitude=HERE[1], max_distance_km=50
        )
        await users.create(display_name="Close", latitude=HERE[0], longitude=HERE[1])
        await users.create(
            display_name="Nearby", latitude=NEARBY[0], longitude=NEARBY[1]
        )
        await users.create(display_name="Berlin", latitude=FAR[0], longitude=FAR[1])

        found = names(await feed(client, await auth(me)))
        assert "Close" in found
        assert "Berlin" not in found

    async def test_widening_distance_reveals_more_people(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create(
            latitude=HERE[0], longitude=HERE[1], max_distance_km=10
        )
        await users.create(display_name="Berlin", latitude=FAR[0], longitude=FAR[1])
        headers = await auth(me)

        assert "Berlin" not in names(await feed(client, headers))

        response = await client.patch(
            f"{API}/preferences", headers=headers, json={"max_distance_km": 20000}
        )
        assert response.status_code == 200
        assert "Berlin" in names(await feed(client, headers))

    async def test_results_are_ordered_by_distance(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create(
            latitude=HERE[0], longitude=HERE[1], max_distance_km=20000
        )
        await users.create(display_name="Far", latitude=FAR[0], longitude=FAR[1])
        await users.create(
            display_name="Middle", latitude=NEARBY[0], longitude=NEARBY[1]
        )
        await users.create(display_name="Near", latitude=HERE[0], longitude=HERE[1])

        profiles = await feed(client, await auth(me))
        order = [p["display_name"] for p in profiles]
        assert order == ["Near", "Middle", "Far"], order


class TestPagination:
    async def test_cursor_walks_the_feed_without_repeats(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create()
        for i in range(7):
            await users.create(display_name=f"P{i}")
        headers = await auth(me)

        first = await client.get(
            f"{API}/discovery", headers=headers, params={"limit": 3}
        )
        page1 = first.json()
        assert len(page1["profiles"]) == 3

        second = await client.get(
            f"{API}/discovery",
            headers=headers,
            params={"limit": 3, "cursor": page1["next_cursor"]},
        )
        page2 = second.json()

        ids1 = {p["user_id"] for p in page1["profiles"]}
        ids2 = {p["user_id"] for p in page2["profiles"]}
        assert not (ids1 & ids2), "a profile appeared on two pages"

    async def test_exhausted_feed_reports_itself(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create()
        response = await client.get(f"{API}/discovery", headers=await auth(me))
        body = response.json()
        assert body["profiles"] == []
        assert body["exhausted"] is True


class TestFeedPrivacy:
    async def test_payload_never_contains_coordinates_or_account_data(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create()
        await users.create(display_name="Other", latitude=12.98, longitude=77.61)

        response = await client.get(f"{API}/discovery", headers=await auth(me))
        raw = json.dumps(response.json())

        for forbidden in (
            "latitude",
            "longitude",
            "12.98",
            "77.61",
            "email",
            "hashed_password",
            "date_of_birth",
            "moderation_status",
            "is_admin",
            "token_version",
        ):
            assert forbidden not in raw, f"{forbidden!r} leaked into the feed"

    async def test_distance_is_rounded_not_exact(
        self, client: AsyncClient, users, auth
    ):
        me = await users.create(latitude=HERE[0], longitude=HERE[1])
        await users.create(display_name="Other", latitude=13.0100, longitude=77.6300)

        profiles = await feed(client, await auth(me))
        distance = profiles[0]["distance_km"]
        # An integer, never a float with metre-level precision.
        assert isinstance(distance, int)

    async def test_only_approved_photos_are_exposed(
        self, client: AsyncClient, users, auth, session_factory
    ):
        from sqlalchemy import select

        from app.models import ModerationStatus, Profile, ProfilePhoto

        me = await users.create()
        other = await users.create(display_name="Other")

        # Add a second, rejected photo to the other user.
        async with session_factory() as db:
            profile = (
                await db.execute(select(Profile).where(Profile.user_id == other.id))
            ).scalar_one()
            db.add(
                ProfilePhoto(
                    profile_id=profile.id,
                    storage_key=f"photos/{other.id}/rejected.png",
                    content_type="image/png",
                    byte_size=10,
                    position=1,
                    moderation_status=ModerationStatus.REJECTED,
                    upload_confirmed=True,
                )
            )
            await db.commit()

        profiles = await feed(client, await auth(me))
        urls = [photo["url"] for photo in profiles[0]["photos"]]
        assert len(urls) == 1
        assert not any("rejected" in url for url in urls)
