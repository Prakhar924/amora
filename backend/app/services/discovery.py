"""The discovery feed.

One SQL statement produces the candidate page. Every exclusion is applied in
the database rather than by filtering in Python, so paging stays correct and a
hidden user can never appear in a later page.

Exclusions, in order of cheapness:

1. Self.
2. Accounts that are not `active`, are deleted, or are currently suspended.
3. Profiles with `is_discoverable = false` or `show_only_to_matches = true`.
4. Profiles with no approved, confirmed photo (nothing to show on a card).
5. Any pair with a `likes` row in either direction — this is the
   duplicate-recommendation filter, and it also hides anyone already matched.
6. Any pair with a `blocks` row in either direction.
7. The viewer's gender / age / distance preferences.

Distance uses a bounding-box prefilter on the indexed `(latitude, longitude)`
columns, then an exact haversine for ordering. That keeps the query planner on
an index scan without needing PostGIS.
"""

from __future__ import annotations

import base64
import json
import math
from datetime import date

from sqlalchemy import Float, and_, cast, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Block,
    Like,
    ModerationStatus,
    Profile,
    ProfilePhoto,
    User,
    UserPreference,
    UserStatus,
)
from app.services.age import dob_bounds_for_age_range

EARTH_RADIUS_KM = 6371.0
# Distances are reported rounded to this many km so that sampling the feed
# from multiple positions cannot triangulate someone's address.
DISTANCE_ROUNDING_KM = 1


def _haversine_km_sql(lat: float, lon: float):
    """Great-circle distance between a fixed point and `profiles` rows, in SQL."""
    lat1 = func.radians(literal(lat))
    lon1 = func.radians(literal(lon))
    lat2 = func.radians(cast(Profile.latitude, Float))
    lon2 = func.radians(cast(Profile.longitude, Float))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = func.power(func.sin(dlat / 2), 2) + func.cos(lat1) * func.cos(lat2) * func.power(
        func.sin(dlon / 2), 2
    )
    return literal(EARTH_RADIUS_KM) * 2 * func.asin(func.least(1.0, func.sqrt(a)))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Python twin of `_haversine_km_sql`, used for seeding and tests."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(min(1.0, math.sqrt(a)))


def _bounding_box(lat: float, lon: float, radius_km: float):
    """A latitude/longitude window that fully contains the radius."""
    lat_delta = radius_km / 111.045
    # Longitude degrees shrink with latitude; guard against the poles.
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    lon_delta = radius_km / (111.045 * cos_lat)
    return (
        max(-90.0, lat - lat_delta),
        min(90.0, lat + lat_delta),
        max(-180.0, lon - lon_delta),
        min(180.0, lon + lon_delta),
    )


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return max(0, int(payload.get("o", 0)))
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0


async def fetch_candidates(
    db: AsyncSession,
    viewer: User,
    limit: int | None = None,
    cursor: str | None = None,
) -> tuple[list[tuple[Profile, float | None]], int]:
    """Return `[(profile, distance_km_or_None), ...]` plus the next offset."""
    limit = limit or settings.discovery_page_size
    offset = decode_cursor(cursor)

    pref = (
        await db.execute(
            select(UserPreference).where(UserPreference.user_id == viewer.id)
        )
    ).scalar_one_or_none()

    viewer_profile = (
        await db.execute(select(Profile).where(Profile.user_id == viewer.id))
    ).scalar_one_or_none()

    min_age = pref.min_age if pref else settings.minimum_age
    max_age = pref.max_age if pref else 99
    interested_in = list(pref.interested_in) if pref and pref.interested_in else []
    max_distance = pref.max_distance_km if pref else 50

    # --- Base: joinable, visible, adult, active accounts ----------------
    conditions = [
        User.id != viewer.id,
        User.status == UserStatus.ACTIVE,
        User.deleted_at.is_(None),
        Profile.is_discoverable.is_(True),
        Profile.show_only_to_matches.is_(False),
        Profile.completed_at.is_not(None),
    ]

    # Suspensions expire, so honour the timestamp rather than status alone.
    conditions.append(
        or_(User.suspended_until.is_(None), User.suspended_until <= func.now())
    )

    # 18+ gate, independent of the viewer's own age filter. The *latest*
    # permissible date of birth is the adult cutoff: anyone born after it is
    # under 18.
    _, adult_cutoff = dob_bounds_for_age_range(
        settings.minimum_age, settings.maximum_age
    )
    conditions.append(User.date_of_birth <= adult_cutoff)

    # Viewer's age window, expressed as a date-of-birth range.
    earliest_dob, latest_dob = dob_bounds_for_age_range(min_age, max_age)
    conditions.append(User.date_of_birth >= earliest_dob)
    conditions.append(User.date_of_birth <= latest_dob)

    # Gender preference. An empty list means "show everyone".
    if interested_in:
        conditions.append(Profile.gender.in_(interested_in))

    # --- Must have at least one approved, uploaded photo -----------------
    has_photo = (
        select(ProfilePhoto.id)
        .where(
            ProfilePhoto.profile_id == Profile.id,
            ProfilePhoto.upload_confirmed.is_(True),
            ProfilePhoto.moderation_status == ModerationStatus.APPROVED,
        )
        .limit(1)
        .exists()
    )
    conditions.append(has_photo)

    # --- Never re-show a profile this viewer has already judged ----------
    already_swiped = (
        select(Like.id)
        .where(Like.actor_id == viewer.id, Like.target_id == User.id)
        .limit(1)
        .exists()
    )
    conditions.append(~already_swiped)

    # Also hide anyone who has already passed on the viewer: showing them
    # would burn a swipe that can never become a match.
    passed_on_viewer = (
        select(Like.id)
        .where(
            Like.actor_id == User.id,
            Like.target_id == viewer.id,
            Like.action == "pass",
        )
        .limit(1)
        .exists()
    )
    conditions.append(~passed_on_viewer)

    # --- Blocks, in either direction -------------------------------------
    blocked = (
        select(Block.id)
        .where(
            or_(
                and_(Block.blocker_id == viewer.id, Block.blocked_id == User.id),
                and_(Block.blocker_id == User.id, Block.blocked_id == viewer.id),
            )
        )
        .limit(1)
        .exists()
    )
    conditions.append(~blocked)

    # --- Distance ---------------------------------------------------------
    viewer_has_location = (
        viewer_profile is not None
        and viewer_profile.latitude is not None
        and viewer_profile.longitude is not None
    )

    if viewer_has_location:
        lat, lon = viewer_profile.latitude, viewer_profile.longitude
        min_lat, max_lat, min_lon, max_lon = _bounding_box(lat, lon, max_distance)
        distance_expr = _haversine_km_sql(lat, lon).label("distance_km")
        conditions += [
            Profile.latitude.is_not(None),
            Profile.longitude.is_not(None),
            Profile.latitude.between(min_lat, max_lat),
            Profile.longitude.between(min_lon, max_lon),
        ]
        # The bounding box is only a prefilter; the haversine comparison in the
        # WHERE clause is what enforces the exact radius.
        stmt = (
            select(Profile, distance_expr)
            .join(User, User.id == Profile.user_id)
            .where(and_(*conditions), _haversine_km_sql(lat, lon) <= max_distance)
            .order_by(distance_expr.asc(), Profile.created_at.desc())
        )
    else:
        # No location yet: fall back to recency so onboarding still has a feed.
        stmt = (
            select(Profile, literal(None).label("distance_km"))
            .join(User, User.id == Profile.user_id)
            .where(and_(*conditions))
            .order_by(User.last_active_at.desc().nullslast(), Profile.created_at.desc())
        )

    stmt = stmt.offset(offset).limit(limit)
    rows = (await db.execute(stmt)).all()

    results: list[tuple[Profile, float | None]] = []
    for profile, distance in rows:
        rounded = None
        if distance is not None:
            rounded = max(
                DISTANCE_ROUNDING_KM, round(float(distance) / DISTANCE_ROUNDING_KM)
            )
        results.append((profile, rounded))

    return results, offset + len(rows)


def profile_age(user_dob: date) -> int:
    from app.services.age import age_on

    return age_on(user_dob)
