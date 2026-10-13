"""In-app notification feed."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import NotificationType


class Notification(Base, UUIDPrimaryKey, Timestamps):
    """A notification addressed to one user.

    Rows are written in the same transaction as the event that caused them, so
    a match notification can never go missing because a side-channel failed.
    Delivery to a live client happens over the WebSocket; this table is the
    durable record and the source for the notification screen.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_unread", "user_id", "read_at"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(
        SAEnum(
            NotificationType,
            name="notification_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(140), nullable=False)
    body: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # Deep-link payload, e.g. {"match_id": "...", "conversation_id": "..."}.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
