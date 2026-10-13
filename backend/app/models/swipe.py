"""Likes/passes and the mutual-match record.

Race safety
-----------
Two users can like each other at the exact same instant. Rather than guarding
that with application-level locking, `matches` stores the pair as an *ordered*
tuple (`user_a_id` < `user_b_id`, enforced by a CHECK constraint) with a UNIQUE
constraint on the pair. Both concurrent transactions therefore attempt to
insert the byte-identical row, Postgres lets exactly one win, and the loser
catches the IntegrityError and re-reads the winner's row. See
`app.services.matching.register_like`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import LikeAction

if TYPE_CHECKING:
    from app.models.messaging import Conversation


class Like(Base, UUIDPrimaryKey, Timestamps):
    """A swipe decision.

    Likes and passes share one table so that "has this pair already been
    judged?" — the duplicate-recommendation filter in discovery — is a single
    indexed lookup rather than a union of two tables.
    """

    __tablename__ = "likes"
    __table_args__ = (
        # One decision per ordered pair. A re-swipe updates this row.
        UniqueConstraint("actor_id", "target_id", name="uq_like_actor_target"),
        CheckConstraint("actor_id <> target_id", name="ck_like_no_self"),
        # Covers the "did the target already like me?" reciprocity probe.
        Index("ix_likes_target_action", "target_id", "action"),
        Index("ix_likes_actor_created", "actor_id", "created_at"),
    )

    actor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[LikeAction] = mapped_column(
        SAEnum(
            LikeAction, name="like_action", values_callable=lambda e: [m.value for m in e]
        ),
        nullable=False,
    )


class Match(Base, UUIDPrimaryKey, Timestamps):
    """A mutual like.

    The pair is always stored with `user_a_id < user_b_id` so that the UNIQUE
    constraint makes a duplicate match impossible regardless of which side
    swiped first.
    """

    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_match_pair"),
        CheckConstraint("user_a_id < user_b_id", name="ck_match_canonical_order"),
        Index("ix_matches_user_a", "user_a_id", "unmatched_at"),
        Index("ix_matches_user_b", "user_b_id", "unmatched_at"),
    )

    user_a_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_b_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Soft-delete: unmatching must not destroy the audit trail that a
    # moderator may need when reviewing a report about the conversation.
    unmatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    unmatched_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    conversation: Mapped[Conversation | None] = relationship(
        "Conversation",
        back_populates="match",
        uselist=False,
        cascade="all, delete-orphan",
    )

    @staticmethod
    def canonical_pair(
        first: uuid.UUID, second: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Order a pair of user ids so the UNIQUE constraint can do its job."""
        return (first, second) if str(first) < str(second) else (second, first)

    @property
    def is_active(self) -> bool:
        return self.unmatched_at is None

    def other_user_id(self, me: uuid.UUID) -> uuid.UUID:
        return self.user_b_id if self.user_a_id == me else self.user_a_id
