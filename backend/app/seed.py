"""Development seed data.

Creates the interest vocabulary, an admin account, and a set of synthetic
dating profiles so discovery, matching, and chat can be exercised locally.

Everything here is fabricated. No real person's name, photo, biography, or
location is used: display names are drawn from a fixed invented list, photos
are generated gradient placeholders, and coordinates are jittered around city
centres. Running this against a production database is refused.

Idempotent: re-running updates the interest vocabulary and skips users that
already exist.

    python -m app.seed            # seed
    python -m app.seed --reset    # delete seeded users first, then seed
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import math
import random
import sys
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal, engine
from app.models import (
    Gender,
    Interest,
    LikeAction,
    ModerationStatus,
    Profile,
    ProfilePhoto,
    User,
    UserInterest,
    UserPreference,
    UserStatus,
)
from app.services import storage
from app.services.matching import register_like

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed")

# Every seeded login uses this password.
DEMO_PASSWORD = "DemoPass123!"
SEED_EMAIL_DOMAIN = "amora.dev"

# A deterministic seed keeps runs reproducible, so a bug found locally can be
# reproduced from the same data.
RNG = random.Random(20260413)


INTERESTS: list[tuple[str, str, str]] = [
    # (slug, label, category)
    ("hiking", "Hiking", "outdoors"),
    ("climbing", "Climbing", "outdoors"),
    ("cycling", "Cycling", "outdoors"),
    ("running", "Running", "fitness"),
    ("yoga", "Yoga", "fitness"),
    ("weightlifting", "Weightlifting", "fitness"),
    ("swimming", "Swimming", "fitness"),
    ("live-music", "Live music", "music"),
    ("vinyl", "Vinyl records", "music"),
    ("playing-guitar", "Playing guitar", "music"),
    ("techno", "Techno", "music"),
    ("jazz", "Jazz", "music"),
    ("cooking", "Cooking", "food"),
    ("baking", "Baking", "food"),
    ("coffee", "Speciality coffee", "food"),
    ("wine", "Wine", "food"),
    ("street-food", "Street food", "food"),
    ("vegetarian", "Vegetarian food", "food"),
    ("reading", "Reading", "culture"),
    ("poetry", "Poetry", "culture"),
    ("film", "Film", "culture"),
    ("theatre", "Theatre", "culture"),
    ("museums", "Museums", "culture"),
    ("photography", "Photography", "creative"),
    ("painting", "Painting", "creative"),
    ("pottery", "Pottery", "creative"),
    ("writing", "Writing", "creative"),
    ("travel", "Travel", "lifestyle"),
    ("languages", "Languages", "lifestyle"),
    ("volunteering", "Volunteering", "lifestyle"),
    ("gardening", "Gardening", "lifestyle"),
    ("dogs", "Dogs", "lifestyle"),
    ("cats", "Cats", "lifestyle"),
    ("board-games", "Board games", "play"),
    ("video-games", "Video games", "play"),
    ("chess", "Chess", "play"),
    ("football", "Football", "sport"),
    ("cricket", "Cricket", "sport"),
    ("tennis", "Tennis", "sport"),
    ("surfing", "Surfing", "sport"),
    ("startups", "Startups", "work"),
    ("design", "Design", "work"),
    ("coding", "Coding", "work"),
    ("astronomy", "Astronomy", "curiosity"),
    ("history", "History", "curiosity"),
    ("podcasts", "Podcasts", "curiosity"),
]

# Invented given names, not drawn from any real dataset.
NAMES_A = [
    "Mira", "Nova", "Zola", "Ines", "Kiva", "Dalia", "Suri", "Rhea",
    "Noor", "Elia", "Tava", "Juno", "Lark", "Vesna", "Oona", "Marei",
]
NAMES_B = [
    "Arlo", "Kael", "Dmitri", "Ravi", "Soren", "Idris", "Tobi", "Nilo",
    "Emre", "Jorin", "Cass", "Vikram", "Osei", "Lucan", "Bodhi", "Amrit",
]
NAMES_C = ["Wren", "Sage", "Ash", "Rue", "Indigo", "Marlow", "Quill", "Ember"]

CITIES = [
    # (city, country, lat, lon)
    ("Bengaluru", "IN", 12.9716, 77.5946),
    ("Mumbai", "IN", 19.0760, 72.8777),
    ("Delhi", "IN", 28.6139, 77.2090),
    ("Berlin", "DE", 52.5200, 13.4050),
    ("Lisbon", "PT", 38.7223, -9.1393),
    ("Austin", "US", 30.2672, -97.7431),
]

JOBS = [
    "Ceramicist", "Data analyst", "Paramedic", "Sound engineer",
    "Landscape architect", "Pastry chef", "Physiotherapist", "Archivist",
    "Marine biologist", "Bookshop owner", "Civil engineer", "Illustrator",
    "Maths teacher", "Climate researcher", "Luthier", "Radiographer",
]

EDUCATION = [
    "Fine arts", "Computer science", "Nursing", "Architecture",
    "Philosophy", "Mechanical engineering", "Marine biology", "Linguistics",
]

BIOS = [
    "Chasing the best filter coffee in the city. Will absolutely talk your "
    "ear off about pour-over ratios.",
    "Weekend climber, weekday spreadsheet wrangler. Looking for someone to "
    "split a very large pizza with.",
    "I make pots that are slightly lopsided on purpose. Ask me about kiln "
    "disasters.",
    "Long walks, longer playlists. I will make you a mixtape before the "
    "second date.",
    "Trying to read 40 books this year and failing gracefully. Recommend me "
    "something.",
    "Early morning swimmer. I am much nicer after 9am, I promise.",
    "I garden badly and enthusiastically. Three tomatoes so far this season.",
    "Here for the farmers market, the dog park, and a decent argument about "
    "films.",
    "Cycled across two countries once. Still recovering, still planning the "
    "next one.",
    "I know too much about trains and I am not sorry about it.",
    "Jazz on vinyl, cheap noodles, and walking home the long way.",
    "Volunteer dog walker. My ideal Sunday involves zero plans and one "
    "bakery.",
]

PRONOUNS = {
    Gender.WOMAN: "she/her",
    Gender.MAN: "he/him",
    Gender.NONBINARY: "they/them",
    Gender.OTHER: None,
}


# --------------------------------------------------------------------------
# Placeholder photo generation
# --------------------------------------------------------------------------
def _gradient_png(width: int, height: int, seed: int) -> bytes:
    """Build a small gradient PNG with no external image library.

    Written by hand so seeding needs no Pillow dependency: zlib and struct are
    in the standard library, and a PNG is just a few length-prefixed chunks.
    """
    import struct
    import zlib

    rng = random.Random(seed)
    # Two corners of a pleasant gradient, kept mid-to-bright so white overlay
    # text would stay readable in the UI.
    c1 = (rng.randint(90, 225), rng.randint(60, 180), rng.randint(110, 230))
    c2 = (rng.randint(90, 225), rng.randint(60, 180), rng.randint(110, 230))

    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter type 0 for each scanline
        for x in range(width):
            # Diagonal interpolation plus a soft radial highlight.
            t = (x / max(1, width - 1) + y / max(1, height - 1)) / 2
            highlight = 1.0 - min(
                1.0,
                math.hypot(x - width * 0.35, y - height * 0.3)
                / (max(width, height) * 0.8),
            )
            for channel in range(3):
                base = c1[channel] * (1 - t) + c2[channel] * t
                value = base + highlight * 28
                rows.append(max(0, min(255, int(value))))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


def _upload_placeholder(key: str, seed: int) -> int:
    """Put a generated PNG into object storage. Returns its byte size."""
    payload = _gradient_png(480, 640, seed)
    client = storage._client()
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=io.BytesIO(payload),
        ContentType="image/png",
        ContentLength=len(payload),
    )
    return len(payload)


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
async def seed_interests(db) -> dict[str, Interest]:
    existing = {
        i.slug: i for i in (await db.execute(select(Interest))).scalars().all()
    }
    for slug, label, category in INTERESTS:
        if slug in existing:
            # Keep labels current without disturbing user_interests rows.
            existing[slug].label = label
            existing[slug].category = category
            existing[slug].is_active = True
        else:
            interest = Interest(
                slug=slug, label=label, category=category, is_active=True
            )
            db.add(interest)
            existing[slug] = interest
    await db.flush()
    logger.info("Interests: %d in vocabulary", len(existing))
    return existing


def _build_people() -> list[dict]:
    """Compose the synthetic cast.

    Deliberately includes edge cases worth having in a dev database: an
    unverified account, a user with no photos, a hidden profile, and a
    suspended account. Each is a state the UI has to handle.
    """
    people: list[dict] = []

    pool = (
        [(n, Gender.WOMAN) for n in NAMES_A]
        + [(n, Gender.MAN) for n in NAMES_B]
        + [(n, Gender.NONBINARY) for n in NAMES_C]
    )

    for index, (name, gender) in enumerate(pool):
        city, country, lat, lon = CITIES[index % len(CITIES)]
        # Scatter within roughly 12km of the city centre.
        jitter_km = RNG.uniform(0.5, 12.0)
        bearing = RNG.uniform(0, 2 * math.pi)
        d_lat = (jitter_km / 111.045) * math.cos(bearing)
        d_lon = (jitter_km / (111.045 * math.cos(math.radians(lat)))) * math.sin(
            bearing
        )

        age = RNG.randint(21, 44)
        people.append(
            {
                "email": f"{name.lower()}@{SEED_EMAIL_DOMAIN}",
                "name": name,
                "gender": gender,
                "age": age,
                "city": city,
                "country": country,
                "lat": round(lat + d_lat, 6),
                "lon": round(lon + d_lon, 6),
                "bio": RNG.choice(BIOS),
                "job": RNG.choice(JOBS),
                "education": RNG.choice(EDUCATION),
                "height": RNG.randint(152, 193),
                "interests": RNG.sample(
                    [slug for slug, _, _ in INTERESTS], RNG.randint(3, 7)
                ),
                "photos": RNG.randint(1, 3),
                "verified": True,
                "status": UserStatus.ACTIVE,
                "discoverable": True,
            }
        )

    # --- Deliberate edge cases ------------------------------------------
    base_city = CITIES[0]
    people.append(
        {
            "email": f"unverified@{SEED_EMAIL_DOMAIN}",
            "name": "Taryn",
            "gender": Gender.WOMAN,
            "age": 29,
            "city": base_city[0], "country": base_city[1],
            "lat": base_city[2], "lon": base_city[3],
            "bio": "Has not confirmed their email yet.",
            "job": "Barista", "education": "Fine arts", "height": 168,
            "interests": ["coffee", "reading"], "photos": 1,
            # Cannot sign in until verification: exercises that gate.
            "verified": False, "status": UserStatus.PENDING_VERIFICATION,
            "discoverable": True,
        }
    )
    people.append(
        {
            "email": f"nophotos@{SEED_EMAIL_DOMAIN}",
            "name": "Dorian",
            "gender": Gender.MAN,
            "age": 33,
            "city": base_city[0], "country": base_city[1],
            "lat": base_city[2], "lon": base_city[3],
            "bio": "Profile without photos, so it stays out of discovery.",
            "job": "Archivist", "education": "History", "height": 180,
            "interests": ["history", "chess"],
            # No photos => incomplete profile => excluded from the feed.
            "photos": 0,
            "verified": True, "status": UserStatus.ACTIVE, "discoverable": True,
        }
    )
    people.append(
        {
            "email": f"hidden@{SEED_EMAIL_DOMAIN}",
            "name": "Calla",
            "gender": Gender.NONBINARY,
            "age": 27,
            "city": base_city[0], "country": base_city[1],
            "lat": base_city[2], "lon": base_city[3],
            "bio": "Has switched off discovery in privacy settings.",
            "job": "Illustrator", "education": "Design", "height": 171,
            "interests": ["painting", "cats"], "photos": 2,
            "verified": True, "status": UserStatus.ACTIVE,
            # Invisible in discovery, but existing chats keep working.
            "discoverable": False,
        }
    )
    people.append(
        {
            "email": f"suspended@{SEED_EMAIL_DOMAIN}",
            "name": "Rovan",
            "gender": Gender.MAN,
            "age": 36,
            "city": base_city[0], "country": base_city[1],
            "lat": base_city[2], "lon": base_city[3],
            "bio": "Suspended account, used to test enforcement.",
            "job": "Promoter", "education": "Philosophy", "height": 184,
            "interests": ["techno"], "photos": 1,
            "verified": True, "status": UserStatus.SUSPENDED,
            "discoverable": True,
        }
    )
    return people


async def seed_users(db, interests: dict[str, Interest]) -> list[User]:
    created: list[User] = []
    now = datetime.now(UTC)

    # Hash once: bcrypt is intentionally slow and every demo user shares the
    # same password, so hashing per user would dominate the seed runtime.
    shared_hash = hash_password(DEMO_PASSWORD)

    # --- Admin ----------------------------------------------------------
    admin_email = f"admin@{SEED_EMAIL_DOMAIN}"
    admin = (
        await db.execute(select(User).where(User.email == admin_email))
    ).scalar_one_or_none()
    if admin is None:
        admin = User(
            email=admin_email,
            hashed_password=shared_hash,
            date_of_birth=datetime(1990, 6, 15, tzinfo=UTC),
            status=UserStatus.ACTIVE,
            is_admin=True,
            email_verified_at=now,
            terms_accepted_at=now,
            privacy_accepted_at=now,
        )
        db.add(admin)
        await db.flush()
        db.add(
            Profile(
                user_id=admin.id,
                display_name="Amora Moderation",
                gender=Gender.OTHER,
                bio="Staff account for moderation.",
                is_discoverable=False,
            )
        )
        db.add(UserPreference(user_id=admin.id, interested_in=[]))
        await db.flush()
        logger.info("Admin account: %s / %s", admin_email, DEMO_PASSWORD)

    storage.ensure_bucket()

    for index, person in enumerate(_build_people()):
        existing = (
            await db.execute(select(User).where(User.email == person["email"]))
        ).scalar_one_or_none()
        if existing is not None:
            created.append(existing)
            continue

        dob = date.today() - timedelta(days=person["age"] * 365 + RNG.randint(0, 300))
        user = User(
            email=person["email"],
            hashed_password=shared_hash,
            date_of_birth=datetime.combine(dob, datetime.min.time(), tzinfo=UTC),
            status=person["status"],
            email_verified_at=now if person["verified"] else None,
            terms_accepted_at=now,
            privacy_accepted_at=now,
            last_active_at=now - timedelta(hours=RNG.randint(0, 72)),
        )
        if person["status"] == UserStatus.SUSPENDED:
            user.suspended_until = now + timedelta(days=7)
            user.suspension_reason = "Seeded suspended account for testing."
        db.add(user)
        await db.flush()

        profile = Profile(
            user_id=user.id,
            display_name=person["name"],
            gender=person["gender"],
            pronouns=PRONOUNS.get(person["gender"]),
            bio=person["bio"],
            job_title=person["job"],
            education=person["education"],
            height_cm=person["height"],
            latitude=person["lat"],
            longitude=person["lon"],
            city=person["city"],
            country=person["country"],
            location_updated_at=now,
            is_discoverable=person["discoverable"],
        )
        db.add(profile)
        await db.flush()

        for slug in person["interests"]:
            interest = interests.get(slug)
            if interest is not None:
                db.add(
                    UserInterest(profile_id=profile.id, interest_id=interest.id)
                )

        for position in range(person["photos"]):
            key = f"photos/{user.id}/seed-{position}.png"
            size = _upload_placeholder(key, seed=index * 10 + position)
            db.add(
                ProfilePhoto(
                    profile_id=profile.id,
                    storage_key=key,
                    content_type="image/png",
                    byte_size=size,
                    width=480,
                    height=640,
                    position=position,
                    # Pre-approved: a dev database should have a usable feed
                    # without someone first clicking through the admin queue.
                    moderation_status=ModerationStatus.APPROVED,
                    upload_confirmed=True,
                    moderated_at=now,
                )
            )

        # Only a profile that could actually render a card counts as complete.
        if person["photos"] > 0:
            profile.completed_at = now

        db.add(
            UserPreference(
                user_id=user.id,
                # Deliberately broad: narrow seeded filters combined with a
                # small cast leave the demo feed nearly empty, which looks
                # like a bug rather than a working filter.
                interested_in=[],
                min_age=max(settings.minimum_age, person["age"] - 14),
                max_age=min(99, person["age"] + 16),
                max_distance_km=RNG.choice([100, 200, 500]),
            )
        )
        created.append(user)

    await db.flush()
    logger.info("Users: %d seeded profiles (+1 admin)", len(created))
    return created


async def seed_interactions(db, users: list[User]) -> None:
    """Create likes, a few mutual matches, and some conversation history."""
    eligible = [
        u
        for u in users
        if u.status == UserStatus.ACTIVE
        and u.email
        not in {
            f"nophotos@{SEED_EMAIL_DOMAIN}",
            f"hidden@{SEED_EMAIL_DOMAIN}",
        }
    ]
    if len(eligible) < 6:
        return

    # Guarantee two reliable demo matches so the matches list and chat are
    # never empty on a fresh database.
    demo_pairs = [(eligible[0], eligible[1]), (eligible[2], eligible[3])]
    matched = 0
    for left, right in demo_pairs:
        await register_like(db, left.id, right.id, LikeAction.LIKE)
        outcome = await register_like(db, right.id, left.id, LikeAction.LIKE)
        if outcome.match is not None:
            matched += 1

    # Plus a scattering of one-sided likes, which is what discovery filtering
    # and the "someone liked you" notification need to be exercised.
    one_sided = 0
    for actor in eligible[4:14]:
        for target in RNG.sample(eligible, 3):
            if target.id == actor.id:
                continue
            await register_like(db, actor.id, target.id, LikeAction.LIKE)
            one_sided += 1

    await db.flush()
    logger.info("Interactions: %d matches, %d one-sided likes", matched, one_sided)

    # --- Seed a conversation on the first demo match ---------------------
    from app.models import Conversation, Match
    from app.services.chat import authorize_conversation, persist_message

    left, right = demo_pairs[0]
    pair_a, pair_b = Match.canonical_pair(left.id, right.id)
    match = (
        await db.execute(
            select(Match).where(
                Match.user_a_id == pair_a, Match.user_b_id == pair_b
            )
        )
    ).scalar_one_or_none()
    if match is None:
        return
    conversation = (
        await db.execute(
            select(Conversation).where(Conversation.match_id == match.id)
        )
    ).scalar_one_or_none()
    if conversation is None:
        return

    script = [
        (left, "Hey! Your pottery photos are great - do you sell any of it?"),
        (right, "Thank you! Mostly I give the wonky ones to friends."),
        (left, "I would take a wonky mug. Coffee this weekend?"),
        (right, "Saturday morning works. I know a place with good filter coffee."),
    ]
    for sender, body in script:
        context = await authorize_conversation(
            db, conversation.id, sender.id, for_sending=True
        )
        await persist_message(db, context, body)

    await db.flush()
    logger.info("Conversation: %d messages on the first demo match", len(script))


async def reset_seeded(db) -> None:
    """Delete previously seeded accounts so the seed can be re-run cleanly."""
    from app.services.account import purge_user_data

    users = (
        (
            await db.execute(
                select(User).where(User.email.like(f"%@{SEED_EMAIL_DOMAIN}"))
            )
        )
        .scalars()
        .all()
    )
    for user in users:
        await purge_user_data(db, user)
    await db.flush()
    # purge leaves tombstones; for seeded data we remove the rows outright.
    await db.execute(delete(User).where(User.deleted_at.is_not(None)))
    await db.flush()
    logger.info("Reset: removed %d seeded accounts", len(users))


async def main(reset: bool = False) -> None:
    if settings.app_env == "production":
        logger.error(
            "Refusing to seed: APP_ENV is 'production'. Seed data is "
            "synthetic and must never enter a production database."
        )
        sys.exit(1)

    async with SessionLocal() as db:
        if reset:
            await reset_seeded(db)
            await db.commit()

        interests = await seed_interests(db)
        users = await seed_users(db, interests)
        await db.commit()

        await seed_interactions(db, users)
        await db.commit()

    await engine.dispose()
    logger.info(
        "\nSeeding complete.\n"
        "  Sign in with any seeded address and the password %s\n"
        "  Examples: mira@%s, arlo@%s\n"
        "  Admin:    admin@%s\n",
        DEMO_PASSWORD,
        SEED_EMAIL_DOMAIN,
        SEED_EMAIL_DOMAIN,
        SEED_EMAIL_DOMAIN,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed development data")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete previously seeded accounts before seeding",
    )
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
