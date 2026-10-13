"""Account-level records: `users` and `sessions`."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import UserStatus

if TYPE_CHECKING:
    from app.models.profile import Profile, UserPreference


class User(Base, UUIDPrimaryKey, Timestamps):
    """An account. Credentials and PII live here, never in `profiles`.

    Nothing in this table is ever serialized to another user: the public
    projection is built from `Profile` instead.
    """

    __tablename__ = "users"

    # Stored lowercased; uniqueness is enforced case-insensitively by the
    # application normalizing on write plus this unique index.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="user_status", values_callable=lambda e: [m.value for m in e]),
        default=UserStatus.PENDING_VERIFICATION,
        nullable=False,
        index=True,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Date of birth is the single source of truth for age; `age` is always
    # derived so a profile cannot drift out of date as time passes.
    date_of_birth: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Bumped on password change / "log out everywhere" to invalidate all
    # outstanding access tokens without maintaining a blocklist.
    token_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Consent records. Required at registration; retained for compliance.
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Moderation
    suspended_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suspension_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set when the user requests deletion; PII is scrubbed in the same
    # transaction and this marks the tombstone row.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    profile: Mapped[Profile | None] = relationship(
        "Profile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    preference: Mapped[UserPreference | None] = relationship(
        "UserPreference", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    sessions: Mapped[list[Session]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_active_account(self) -> bool:
        return self.status == UserStatus.ACTIVE and self.deleted_at is None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.id} {self.status.value}>"


class Session(Base, UUIDPrimaryKey, Timestamps):
    """A refresh-token session (one per device / login).

    Only the SHA-256 hash of the refresh token is stored. `family_id` groups
    the rotation chain so that replaying a consumed token can revoke every
    descendant session at once.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_active", "user_id", "revoked_at"),
        Index("ix_sessions_family", "family_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), default=uuid.uuid4, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Coarse device info for the "active sessions" screen. Deliberately not an
    # IP address, which we do not retain.
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)

    user: Mapped[User] = relationship("User", back_populates="sessions")

    def is_usable(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now
