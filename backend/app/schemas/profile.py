"""Profile, photo, and preference schemas.

Two distinct profile projections exist and the difference is the privacy
boundary:

* `MyProfile`  — the owner's view. Includes pending/rejected photos and the
                 coarse location the owner set.
* `PublicProfile` — what any other user may see. No coordinates, no email, no
                 moderation state, and only approved photos.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.models.enums import Gender, ModerationStatus


class InterestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    label: str
    category: str


class PhotoOut(BaseModel):
    """A photo as seen by its owner."""

    id: uuid.UUID
    position: int
    url: str = Field(description="Short-lived presigned GET URL")
    moderation_status: ModerationStatus
    moderation_note: str | None = None
    upload_confirmed: bool
    created_at: datetime


class PublicPhotoOut(BaseModel):
    """A photo as seen by another user. Moderation state is withheld."""

    id: uuid.UUID
    position: int
    url: str


class ProfileUpdate(BaseModel):
    """Partial update. Every field is optional; omitted fields are untouched."""

    display_name: str | None = Field(default=None, min_length=2, max_length=60)
    bio: str | None = Field(default=None, max_length=1000)
    gender: Gender | None = None
    pronouns: str | None = Field(default=None, max_length=40)
    job_title: str | None = Field(default=None, max_length=100)
    education: str | None = Field(default=None, max_length=100)
    height_cm: int | None = Field(default=None, ge=100, le=260)
    interests: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Interest slugs. Replaces the existing set when provided.",
    )
    is_discoverable: bool | None = None
    show_only_to_matches: bool | None = None

    @field_validator("bio", "pronouns", "job_title", "education")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("display_name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Display name must be at least 2 characters")
        return cleaned


class LocationUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, min_length=2, max_length=2)


class PreferenceUpdate(BaseModel):
    interested_in: list[Gender] | None = Field(default=None, max_length=4)
    min_age: int | None = Field(default=None, ge=18, le=120)
    max_age: int | None = Field(default=None, ge=18, le=120)
    max_distance_km: int | None = Field(default=None, ge=1, le=20000)
    notify_new_match: bool | None = None
    notify_new_message: bool | None = None
    notify_new_like: bool | None = None


class PreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    interested_in: list[str]
    min_age: int
    max_age: int
    max_distance_km: int
    notify_new_match: bool
    notify_new_message: bool
    notify_new_like: bool


class MyProfile(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    age: int
    bio: str | None
    gender: Gender
    pronouns: str | None
    job_title: str | None
    education: str | None
    height_cm: int | None
    city: str | None
    country: str | None
    is_discoverable: bool
    show_only_to_matches: bool
    is_complete: bool
    interests: list[InterestOut]
    photos: list[PhotoOut]


class PublicProfile(BaseModel):
    """The only profile shape ever returned for somebody else.

    Note what is absent: coordinates, email, user status, moderation detail.
    `distance_km` is rounded so a precise location cannot be triangulated by
    sampling the feed from several positions.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    age: int
    bio: str | None
    gender: Gender
    pronouns: str | None
    job_title: str | None
    education: str | None
    height_cm: int | None
    city: str | None
    distance_km: int | None = Field(
        default=None, description="Rounded great-circle distance; never exact"
    )
    interests: list[InterestOut]
    photos: list[PublicPhotoOut]


class PhotoUploadRequest(BaseModel):
    content_type: str = Field(description="image/jpeg, image/png, or image/webp")
    byte_size: int = Field(gt=0, le=settings.max_photo_bytes)

    @field_validator("content_type")
    @classmethod
    def _allowed_type(cls, value: str) -> str:
        allowed = {"image/jpeg", "image/png", "image/webp"}
        normalized = value.split(";")[0].strip().lower()
        if normalized not in allowed:
            raise ValueError(f"Content type must be one of: {', '.join(sorted(allowed))}")
        return normalized


class PhotoUploadTicket(BaseModel):
    """Everything the client needs to PUT the file straight to object storage."""

    photo_id: uuid.UUID
    upload_url: str
    method: str = "PUT"
    headers: dict[str, str]
    expires_in: int


class PhotoReorderRequest(BaseModel):
    # Full ordered list of photo ids. Index 0 becomes the primary photo.
    photo_ids: list[uuid.UUID] = Field(min_length=1, max_length=settings.max_photos_per_user)
