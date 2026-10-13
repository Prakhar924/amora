"""Conversations, messages, and per-participant read state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.swipe import Match


class Conversation(Base, UUIDPrimaryKey, Timestamps):
    """The chat thread belonging to a match.

    A conversation exists only for a match, which is what makes "only matched
    users can talk" structural: there is no way to address a thread that no
    match created.
    """

    __tablename__ = "conversations"

    match_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("matches.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    # Denormalized for ordering the chat list without aggregating `messages`.
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_message_preview: Mapped[str | None] = mapped_column(String(160), nullable=True)

    match: Mapped[Match] = relationship("Match", back_populates="conversation")
    messages: Mapped[list[Message]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base, UUIDPrimaryKey, Timestamps):
    """A single chat message.

    `client_nonce` is supplied by the sender and unique per conversation, which
    makes sending idempotent: a WebSocket retry after a flaky reconnect cannot
    create a duplicate message.
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "client_nonce", name="uq_message_conversation_nonce"
        ),
        CheckConstraint("char_length(body) > 0", name="ck_message_body_not_empty"),
        # The history query: newest-first within a conversation.
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_sender", "sender_id"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    client_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Soft delete, so "delete for me" never rewrites the other side's history.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    conversation: Mapped[Conversation] = relationship(
        "Conversation", back_populates="messages"
    )


class MessageRead(Base, UUIDPrimaryKey, Timestamps):
    """Read receipts and unread counters, one row per (conversation, user).

    Storing a high-water mark (`last_read_at` plus a cached `unread_count`) is
    far cheaper than a per-message read table and is all the UI needs.
    """

    __tablename__ = "message_reads"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_read_conversation_user"),
        Index("ix_reads_user_unread", "user_id", "unread_count"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    last_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_read_message_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
