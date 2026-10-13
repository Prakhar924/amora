"""Profile, photo, and preference management."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import ActiveUser, Db, OnboardedUser
from app.core.config import settings
from app.models import (
    Block,
    Interest,
    Match,
    ModerationStatus,
    Profile,
    ProfilePhoto,
    User,
    UserInterest,
    UserPreference,
    UserStatus,
)
from app.schemas.profile import (
    InterestOut,
    LocationUpdate,
    MyProfile,
    PhotoReorderRequest,
    PhotoUploadRequest,
    PhotoUploadTicket,
    PreferenceOut,
    PreferenceUpdate,
    ProfileUpdate,
    PublicProfile,
)
from app.services import storage
from app.services.discovery import DISTANCE_ROUNDING_KM, haversine_km
from app.services.serializers import my_profile, public_profile

router = APIRouter(tags=["profiles"])


async def _load_profile(db: Db, user: User) -> Profile:
    """Load the caller's profile with every relationship the serializer needs.

    `populate_existing` is essential: without it a second call in the same
    request returns the identity-mapped instance with its *already loaded*
    collections, so a profile re-read right after writing new interests or
    photos would serialize the pre-update collection.
    """
    profile = (
        await db.execute(
            select(Profile)
            .where(Profile.user_id == user.id)
            .execution_options(populate_existing=True)
            .options(
                selectinload(Profile.photos),
                selectinload(Profile.interests).selectinload(UserInterest.interest),
                selectinload(Profile.user),
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return profile


async def _refresh_completeness(db: Db, profile: Profile) -> None:
    """A profile is complete once it can actually be shown on a card.

    The flush is required: the session is configured with `autoflush=False`,
    so a photo whose `upload_confirmed` was just set in memory would not be
    visible to the COUNT below and the profile would stay incomplete.
    """
    await db.flush()

    approved_photos = (
        await db.execute(
            select(func.count(ProfilePhoto.id)).where(
                ProfilePhoto.profile_id == profile.id,
                ProfilePhoto.upload_confirmed.is_(True),
                ProfilePhoto.moderation_status == ModerationStatus.APPROVED,
            )
        )
    ).scalar_one()

    is_complete = bool(
        profile.display_name
        and len(profile.display_name) >= 2
        and profile.gender is not None
        and approved_photos > 0
    )

    if is_complete and profile.completed_at is None:
        profile.completed_at = datetime.now(UTC)
    elif not is_complete:
        profile.completed_at = None


# --------------------------------------------------------------------------
# My profile
# --------------------------------------------------------------------------
@router.get("/profiles/me", response_model=MyProfile, summary="Get my profile")
async def get_my_profile(user: ActiveUser, db: Db) -> MyProfile:
    profile = await _load_profile(db, user)
    return my_profile(profile)


@router.patch("/profiles/me", response_model=MyProfile, summary="Update my profile")
async def update_my_profile(
    payload: ProfileUpdate, user: ActiveUser, db: Db
) -> MyProfile:
    profile = await _load_profile(db, user)

    data = payload.model_dump(exclude_unset=True)
    interest_slugs = data.pop("interests", None)

    for field, value in data.items():
        setattr(profile, field, value)

    if interest_slugs is not None:
        await _replace_interests(db, profile, interest_slugs)

    await _refresh_completeness(db, profile)
    await db.commit()

    return my_profile(await _load_profile(db, user))


async def _replace_interests(db: Db, profile: Profile, slugs: list[str]) -> None:
    """Swap the profile's interest set for `slugs`, ignoring unknown tags."""
    normalized = {s.strip().lower() for s in slugs if s and s.strip()}

    await db.execute(delete(UserInterest).where(UserInterest.profile_id == profile.id))

    if not normalized:
        return

    found = (
        (
            await db.execute(
                select(Interest.id, Interest.slug).where(
                    Interest.slug.in_(normalized), Interest.is_active.is_(True)
                )
            )
        )
        .all()
    )
    unknown = normalized - {slug for _, slug in found}
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown interests: {', '.join(sorted(unknown))}",
        )

    for interest_id, _ in found:
        db.add(UserInterest(profile_id=profile.id, interest_id=interest_id))


@router.put("/profiles/me/location", summary="Update my location")
async def update_location(
    payload: LocationUpdate, user: ActiveUser, db: Db
) -> dict:
    """Store precise coordinates for distance maths.

    These are never returned by any endpoint; discovery exposes only a rounded
    distance derived from them.
    """
    profile = await _load_profile(db, user)
    profile.latitude = payload.latitude
    profile.longitude = payload.longitude
    profile.city = payload.city
    profile.country = payload.country.upper() if payload.country else None
    profile.location_updated_at = datetime.now(UTC)
    await db.commit()
    return {
        "message": "Location updated.",
        "city": profile.city,
        "country": profile.country,
    }


# --------------------------------------------------------------------------
# Preferences
# --------------------------------------------------------------------------
@router.get(
    "/preferences", response_model=PreferenceOut, summary="Get discovery preferences"
)
async def get_preferences(user: ActiveUser, db: Db) -> PreferenceOut:
    pref = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
    ).scalar_one_or_none()
    if pref is None:
        pref = UserPreference(user_id=user.id, interested_in=[])
        db.add(pref)
        await db.commit()
        await db.refresh(pref)
    return PreferenceOut.model_validate(pref)


@router.patch(
    "/preferences", response_model=PreferenceOut, summary="Update discovery preferences"
)
async def update_preferences(
    payload: PreferenceUpdate, user: ActiveUser, db: Db
) -> PreferenceOut:
    pref = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
    ).scalar_one_or_none()
    if pref is None:
        pref = UserPreference(user_id=user.id, interested_in=[])
        db.add(pref)
        await db.flush()

    data = payload.model_dump(exclude_unset=True)
    if "interested_in" in data and data["interested_in"] is not None:
        data["interested_in"] = [
            g.value if hasattr(g, "value") else str(g) for g in data["interested_in"]
        ]

    for field, value in data.items():
        setattr(pref, field, value)

    # Validate the resulting window before the CHECK constraint rejects it, so
    # the client gets a readable message instead of a 500.
    if pref.max_age < pref.min_age:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Maximum age must be greater than or equal to minimum age.",
        )
    if pref.min_age < settings.minimum_age:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Minimum age cannot be below {settings.minimum_age}.",
        )

    await db.commit()
    await db.refresh(pref)
    return PreferenceOut.model_validate(pref)


# --------------------------------------------------------------------------
# Photos
# --------------------------------------------------------------------------
@router.post(
    "/profiles/me/photos",
    response_model=PhotoUploadTicket,
    status_code=status.HTTP_201_CREATED,
    summary="Request a presigned upload for a new photo",
)
async def create_photo_upload(
    payload: PhotoUploadRequest, user: ActiveUser, db: Db
) -> PhotoUploadTicket:
    """Reserve a photo slot and return a presigned PUT.

    The row is created unconfirmed; the client uploads directly to object
    storage and then calls the confirm endpoint. Bytes never transit the API.
    """
    profile = await _load_profile(db, user)

    if len(profile.photos) >= settings.max_photos_per_user:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You can have at most {settings.max_photos_per_user} photos. "
            "Delete one before adding another.",
        )

    next_position = max((p.position for p in profile.photos), default=-1) + 1
    key = storage.build_photo_key(user.id, payload.content_type)

    photo = ProfilePhoto(
        profile_id=profile.id,
        storage_key=key,
        content_type=payload.content_type,
        byte_size=payload.byte_size,
        position=next_position,
        moderation_status=(
            ModerationStatus.PENDING
            if settings.require_photo_moderation
            else ModerationStatus.APPROVED
        ),
        upload_confirmed=False,
    )
    db.add(photo)
    await db.commit()
    await db.refresh(photo)

    ticket = storage.presign_upload(key, payload.content_type, payload.byte_size)
    return PhotoUploadTicket(
        photo_id=photo.id,
        upload_url=ticket["url"],
        headers=ticket["headers"],
        expires_in=ticket["expires_in"],
    )


@router.post(
    "/profiles/me/photos/{photo_id}/confirm",
    response_model=MyProfile,
    summary="Confirm a photo upload completed",
)
async def confirm_photo(photo_id: uuid.UUID, user: ActiveUser, db: Db) -> MyProfile:
    """Verify the object really landed before the photo counts as uploaded."""
    profile = await _load_profile(db, user)
    photo = next((p for p in profile.photos if p.id == photo_id), None)
    if photo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found")

    exists, byte_size = storage.object_exists(photo.storage_key)
    if not exists:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "We could not find that upload. Please try uploading the photo again.",
        )

    photo.upload_confirmed = True
    if byte_size:
        photo.byte_size = byte_size

    await _refresh_completeness(db, profile)
    await db.commit()
    return my_profile(await _load_profile(db, user))


@router.delete(
    "/profiles/me/photos/{photo_id}",
    response_model=MyProfile,
    summary="Delete one of my photos",
)
async def delete_photo(photo_id: uuid.UUID, user: ActiveUser, db: Db) -> MyProfile:
    profile = await _load_profile(db, user)
    photo = next((p for p in profile.photos if p.id == photo_id), None)
    if photo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found")

    key = photo.storage_key
    await db.execute(delete(ProfilePhoto).where(ProfilePhoto.id == photo_id))
    await db.flush()

    # Close the gap left in the ordering so positions stay 0..n-1 and the
    # (profile_id, position) unique constraint cannot be tripped later.
    remaining = (
        (
            await db.execute(
                select(ProfilePhoto)
                .where(ProfilePhoto.profile_id == profile.id)
                .order_by(ProfilePhoto.position)
            )
        )
        .scalars()
        .all()
    )
    # Two passes via a negative offset: positions are unique, so shifting in
    # place could collide with a position that has not moved yet.
    for index, remaining_photo in enumerate(remaining):
        remaining_photo.position = -(index + 1)
    await db.flush()
    for remaining_photo in remaining:
        remaining_photo.position = -remaining_photo.position - 1

    await db.flush()
    await _refresh_completeness(db, profile)
    await db.commit()

    storage.delete_object(key)
    return my_profile(await _load_profile(db, user))


@router.put(
    "/profiles/me/photos/order",
    response_model=MyProfile,
    summary="Reorder my photos",
)
async def reorder_photos(
    payload: PhotoReorderRequest, user: ActiveUser, db: Db
) -> MyProfile:
    profile = await _load_profile(db, user)
    owned = {p.id: p for p in profile.photos}

    if set(payload.photo_ids) != set(owned):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "The list must contain each of your photo ids exactly once.",
        )

    # Same negative-offset trick as above to avoid transient collisions.
    for index, photo_id in enumerate(payload.photo_ids):
        owned[photo_id].position = -(index + 1)
    await db.flush()
    for photo in owned.values():
        photo.position = -photo.position - 1
    await db.commit()

    return my_profile(await _load_profile(db, user))


# --------------------------------------------------------------------------
# Viewing someone else
# --------------------------------------------------------------------------
@router.get(
    "/profiles/{user_id}",
    response_model=PublicProfile,
    summary="View another user's public profile",
)
async def get_public_profile(
    user_id: uuid.UUID, user: OnboardedUser, db: Db
) -> PublicProfile:
    """Fetch a single public profile.

    Blocks are checked in both directions and a 404 is returned rather than a
    403, so a blocked user cannot confirm the account still exists.
    """
    if user_id == user.id:
        profile = await _load_profile(db, user)
        return public_profile(profile)

    blocked = (
        await db.execute(
            select(Block.id).where(
                or_(
                    (Block.blocker_id == user.id) & (Block.blocked_id == user_id),
                    (Block.blocker_id == user_id) & (Block.blocked_id == user.id),
                )
            )
        )
    ).first()
    if blocked is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")

    target = (
        await db.execute(
            select(Profile)
            .join(User, User.id == Profile.user_id)
            .where(
                Profile.user_id == user_id,
                User.status == UserStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
            .options(
                selectinload(Profile.photos),
                selectinload(Profile.interests).selectinload(UserInterest.interest),
                selectinload(Profile.user),
            )
        )
    ).scalar_one_or_none()

    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")

    # A private profile is visible only to existing matches.
    if target.show_only_to_matches or not target.is_discoverable:
        pair_a, pair_b = Match.canonical_pair(user.id, user_id)
        has_match = (
            await db.execute(
                select(Match.id).where(
                    Match.user_a_id == pair_a,
                    Match.user_b_id == pair_b,
                    Match.unmatched_at.is_(None),
                )
            )
        ).first()
        if has_match is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")

    distance = None
    viewer_profile = user.profile
    if (
        viewer_profile is not None
        and viewer_profile.latitude is not None
        and target.latitude is not None
    ):
        exact = haversine_km(
            viewer_profile.latitude,
            viewer_profile.longitude,
            target.latitude,
            target.longitude,
        )
        distance = max(DISTANCE_ROUNDING_KM, round(exact))

    return public_profile(target, distance)


# --------------------------------------------------------------------------
# Interests vocabulary
# --------------------------------------------------------------------------
@router.get(
    "/interests",
    response_model=list[InterestOut],
    summary="The interest vocabulary users can pick from",
)
async def list_interests(user: ActiveUser, db: Db) -> list[InterestOut]:
    rows = (
        (
            await db.execute(
                select(Interest)
                .where(Interest.is_active.is_(True))
                .order_by(Interest.category, Interest.label)
            )
        )
        .scalars()
        .all()
    )
    return [InterestOut.model_validate(r) for r in rows]
