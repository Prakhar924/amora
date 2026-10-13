"""Profile, photos, and discovery preferences."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import Gender, ModerationStatus

if TYPE_CHECKING:
    from app.models.interest import UserInterest
    from app.models.user import User


class Profile(Base, UUIDPrimaryKey, Timestamps):
    """The publicly discoverable part of an account.

    Privacy: `latitude`/`longitude` are precise but are never serialized to
    another user. Discovery returns a rounded distance instead, and
    `city`/`country` are the only location strings ever exposed.
    """

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("char_length(display_name) >= 2", name="ck_profiles_name_len"),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_profiles_lat_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_profiles_lon_range",
        ),
        # Supports the bounding-box prefilter in the discovery query.
        Index("ix_profiles_geo", "latitude", "longitude"),
        Index("ix_profiles_discoverable", "is_discoverable", "gender"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    display_name: Mapped[str] = mapped_column(String(60), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    gender: Mapped[Gender] = mapped_column(
        SAEnum(Gender, name="gender", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    # Free-text, user-supplied. Shown as-is; never used for filtering.
    pronouns: Mapped[str | None] = mapped_column(String(40), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    education: Mapped[str | None] = mapped_column(String(100), nullable=True)
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Location (private) ----------------------------------------------
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    location_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Visibility -------------------------------------------------------
    # False = invisible in discovery, but existing matches and chats still work.
    is_discoverable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Hide the profile from anyone the user has not already matched with.
    show_only_to_matches: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Set once onboarding is finished (name, gender, dob, and one photo).
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship("User", back_populates="profile")
    photos: Mapped[list[ProfilePhoto]] = relationship(
        "ProfilePhoto",
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="ProfilePhoto.position",
    )
    interests: Mapped[list[UserInterest]] = relationship(
        "UserInterest", back_populates="profile", cascade="all, delete-orphan"
    )


class ProfilePhoto(Base, UUIDPrimaryKey, Timestamps):
    """A photo held in S3-compatible object storage.

    Only the object key is persisted. Clients receive short-lived presigned GET
    URLs, so the bucket itself stays private and a leaked URL expires.
    """

    __tablename__ = "profile_photos"
    __table_args__ = (
        UniqueConstraint("profile_id", "position", name="uq_photo_profile_position"),
        Index("ix_photos_moderation", "moderation_status"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Position 0 is the primary photo shown on the discovery card.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    moderation_status: Mapped[ModerationStatus] = mapped_column(
        SAEnum(
            ModerationStatus,
            name="moderation_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ModerationStatus.PENDING,
        nullable=False,
    )
    moderation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    moderated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    moderated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Flipped once the client confirms the presigned PUT succeeded.
    upload_confirmed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    profile: Mapped[Profile] = relationship("Profile", back_populates="photos")

    @property
    def is_publicly_visible(self) -> bool:
        return (
            self.upload_confirmed
            and self.moderation_status == ModerationStatus.APPROVED
        )


class UserPreference(Base, UUIDPrimaryKey, Timestamps):
    """Discovery filters. Exactly one row per user, created at registration."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint("min_age >= 18", name="ck_pref_min_age_18"),
        CheckConstraint("max_age >= min_age", name="ck_pref_age_order"),
        CheckConstraint(
            "max_distance_km > 0 AND max_distance_km <= 20000",
            name="ck_pref_distance_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Which genders to show. An empty list is treated as "everyone".
    interested_in: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)), default=list, nullable=False
    )
    min_age: Mapped[int] = mapped_column(Integer, default=18, nullable=False)
    max_age: Mapped[int] = mapped_column(Integer, default=99, nullable=False)
    max_distance_km: Mapped[int] = mapped_column(Integer, default=50, nullable=False)

    # Notification opt-ins.
    notify_new_match: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_new_message: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    notify_new_like: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="preference")
