"""Trust and safety records: blocks and reports."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import ReportReason, ReportStatus


class Block(Base, UUIDPrimaryKey, Timestamps):
    """A one-directional block that is enforced in both directions.

    A single row hides each user from the other: discovery, matching, and
    messaging all test for a block with either user on either side. Keeping it
    one-directional means we retain who initiated it, which matters for
    moderation review.
    """

    __tablename__ = "blocks"
    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_block_pair"),
        CheckConstraint("blocker_id <> blocked_id", name="ck_block_no_self"),
        Index("ix_blocks_blocked", "blocked_id"),
    )

    blocker_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    blocked_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Report(Base, UUIDPrimaryKey, Timestamps):
    """A user report queued for admin review."""

    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("reporter_id <> reported_id", name="ck_report_no_self"),
        Index("ix_reports_status_created", "status", "created_at"),
        Index("ix_reports_reported", "reported_id"),
    )

    reporter_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reported_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    reason: Mapped[ReportReason] = mapped_column(
        SAEnum(
            ReportReason,
            name="report_reason",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional pointers to the thing being reported, for moderator context.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    photo_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("profile_photos.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[ReportStatus] = mapped_column(
        SAEnum(
            ReportStatus,
            name="report_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=ReportStatus.OPEN,
        nullable=False,
    )
    # Free-text slot for an automated classifier to write a verdict into
    # later, without a schema change.
    auto_signal: Mapped[str | None] = mapped_column(String(100), nullable=True)

    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
