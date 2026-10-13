"""Interests and the profile<->interest join table."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.profile import Profile


class Interest(Base, UUIDPrimaryKey, Timestamps):
    """A curated interest tag.

    Interests come from a seeded vocabulary rather than free text so that
    "shared interests" is a cheap join and the tag list stays moderatable.
    """

    __tablename__ = "interests"

    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="general")
    # Lets an admin retire a tag without breaking existing profile rows.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    users: Mapped[list[UserInterest]] = relationship(
        "UserInterest", back_populates="interest", cascade="all, delete-orphan"
    )


class UserInterest(Base, UUIDPrimaryKey, Timestamps):
    __tablename__ = "user_interests"
    __table_args__ = (
        UniqueConstraint("profile_id", "interest_id", name="uq_user_interest"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    interest_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("interests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    profile: Mapped[Profile] = relationship("Profile", back_populates="interests")
    interest: Mapped[Interest] = relationship("Interest", back_populates="users")
