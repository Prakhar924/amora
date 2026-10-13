"""Model -> schema projection.

Every profile that leaves the API goes through one of these two functions.
Keeping the projection in one place is what makes the privacy guarantee
auditable: `public_profile` has no access path to coordinates or account
fields, so a new endpoint cannot accidentally leak them.
"""

from __future__ import annotations

from app.models import ModerationStatus, Profile
from app.schemas.profile import (
    InterestOut,
    MyProfile,
    PhotoOut,
    PublicPhotoOut,
    PublicProfile,
)
from app.services.age import age_on
from app.services.storage import presign_download


def _interests(profile: Profile) -> list[InterestOut]:
    return [
        InterestOut(
            slug=link.interest.slug,
            label=link.interest.label,
            category=link.interest.category,
        )
        for link in profile.interests
        if link.interest is not None
    ]


def my_profile(profile: Profile) -> MyProfile:
    """The owner's own view: includes pending and rejected photos."""
    return MyProfile(
        id=profile.id,
        user_id=profile.user_id,
        display_name=profile.display_name,
        age=age_on(profile.user.date_of_birth),
        bio=profile.bio,
        gender=profile.gender,
        pronouns=profile.pronouns,
        job_title=profile.job_title,
        education=profile.education,
        height_cm=profile.height_cm,
        city=profile.city,
        country=profile.country,
        is_discoverable=profile.is_discoverable,
        show_only_to_matches=profile.show_only_to_matches,
        is_complete=profile.completed_at is not None,
        interests=_interests(profile),
        photos=[
            PhotoOut(
                id=photo.id,
                position=photo.position,
                url=presign_download(photo.storage_key),
                moderation_status=photo.moderation_status,
                moderation_note=photo.moderation_note,
                upload_confirmed=photo.upload_confirmed,
                created_at=photo.created_at,
            )
            for photo in sorted(profile.photos, key=lambda p: p.position)
        ],
    )


def public_profile(profile: Profile, distance_km: int | None = None) -> PublicProfile:
    """What another user is allowed to see.

    Only approved, confirmed photos are included, and the caller supplies an
    already-rounded distance — this function has no access to coordinates.
    """
    visible_photos = [
        photo
        for photo in sorted(profile.photos, key=lambda p: p.position)
        if photo.upload_confirmed
        and photo.moderation_status == ModerationStatus.APPROVED
    ]
    return PublicProfile(
        id=profile.id,
        user_id=profile.user_id,
        display_name=profile.display_name,
        age=age_on(profile.user.date_of_birth),
        bio=profile.bio,
        gender=profile.gender,
        pronouns=profile.pronouns,
        job_title=profile.job_title,
        education=profile.education,
        height_cm=profile.height_cm,
        # City is coarse enough to show; coordinates never are.
        city=profile.city,
        distance_km=distance_km,
        interests=_interests(profile),
        photos=[
            PublicPhotoOut(
                id=photo.id,
                position=photo.position,
                url=presign_download(photo.storage_key),
            )
            for photo in visible_photos
        ],
    )
