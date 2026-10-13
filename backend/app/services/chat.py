"""Chat authorization and message persistence.

`authorize_conversation` is the single gate for every chat operation, REST and
WebSocket alike. Both transports call it, so the rule "only mutually matched,
unblocked users can exchange messages" cannot be satisfied on one path and
missed on the other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Block,
    Conversation,
    Match,
    Message,
    MessageRead,
    Notification,
    NotificationType,
    Profile,
    User,
    UserPreference,
    UserStatus,
)


class ChatDenied(Exception):
    """Raised when a caller may not touch a conversation.

    `reason` distinguishes "no such thread for you" (404-worthy) from
    "the thread exists but is now read-only" (403-worthy).
    """

    def __init__(self, reason: str, *, not_found: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.not_found = not_found


@dataclass
class ChatContext:
    conversation: Conversation
    match: Match
    me: uuid.UUID
    other_user_id: uuid.UUID
    can_send: bool


async def authorize_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    for_sending: bool,
) -> ChatContext:
    """Resolve a conversation for a user, or raise `ChatDenied`.

    Checks, in order:
      1. The conversation exists.
      2. The caller is one of the two participants of its match.
      3. The match is still active (nobody has unmatched).
      4. Neither side has blocked the other.
      5. The other account is still active.
    """
    conversation = (
        await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if conversation is None:
        raise ChatDenied("Conversation not found")

    match = (
        await db.execute(select(Match).where(Match.id == conversation.match_id))
    ).scalar_one_or_none()
    if match is None:
        raise ChatDenied("Conversation not found")

    # Not a participant: 404, never 403 — do not confirm the thread exists.
    if user_id not in (match.user_a_id, match.user_b_id):
        raise ChatDenied("Conversation not found")

    other_id = match.other_user_id(user_id)

    blocked = (
        await db.execute(
            select(Block.id).where(
                or_(
                    (Block.blocker_id == user_id) & (Block.blocked_id == other_id),
                    (Block.blocker_id == other_id) & (Block.blocked_id == user_id),
                )
            )
        )
    ).first()

    other_active = (
        await db.execute(
            select(User.id).where(
                User.id == other_id,
                User.status == UserStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
        )
    ).first()

    can_send = (
        match.unmatched_at is None and blocked is None and other_active is not None
    )

    if for_sending and not can_send:
        if blocked is not None:
            raise ChatDenied(
                "You can no longer message this person.", not_found=False
            )
        if match.unmatched_at is not None:
            raise ChatDenied(
                "This conversation ended when the match was removed.", not_found=False
            )
        raise ChatDenied("This person is no longer available.", not_found=False)

    # Reading an archived thread stays allowed; sending does not.
    if not for_sending and match.unmatched_at is not None and blocked is not None:
        raise ChatDenied("Conversation not found")

    return ChatContext(
        conversation=conversation,
        match=match,
        me=user_id,
        other_user_id=other_id,
        can_send=can_send,
    )


async def persist_message(
    db: AsyncSession,
    context: ChatContext,
    body: str,
    client_nonce: str | None = None,
) -> tuple[Message, bool]:
    """Insert a message. Returns `(message, created)`.

    Idempotent on `(conversation_id, client_nonce)`: a WebSocket retry after a
    dropped connection returns the original message instead of duplicating it.
    """
    if client_nonce:
        existing = (
            await db.execute(
                select(Message).where(
                    Message.conversation_id == context.conversation.id,
                    Message.client_nonce == client_nonce,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

    message = Message(
        conversation_id=context.conversation.id,
        sender_id=context.me,
        body=body,
        client_nonce=client_nonce,
    )
    db.add(message)

    try:
        await db.flush()
    except IntegrityError:
        # Lost a race against a concurrent retry carrying the same nonce.
        await db.rollback()
        if client_nonce:
            existing = (
                await db.execute(
                    select(Message).where(
                        Message.conversation_id == context.conversation.id,
                        Message.client_nonce == client_nonce,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False
        raise

    now = datetime.now(UTC)
    context.conversation.last_message_at = now
    context.conversation.last_message_preview = body[:160]

    # Bump the recipient's unread counter atomically, so simultaneous sends
    # from two devices cannot lose an increment.
    result = await db.execute(
        update(MessageRead)
        .where(
            MessageRead.conversation_id == context.conversation.id,
            MessageRead.user_id == context.other_user_id,
        )
        .values(unread_count=MessageRead.unread_count + 1)
    )
    if result.rowcount == 0:
        db.add(
            MessageRead(
                conversation_id=context.conversation.id,
                user_id=context.other_user_id,
                unread_count=1,
            )
        )

    if await _wants_message_notification(db, context.other_user_id):
        sender_name = (
            await db.execute(
                select(Profile.display_name).where(Profile.user_id == context.me)
            )
        ).scalar_one_or_none() or "Someone"
        db.add(
            Notification(
                user_id=context.other_user_id,
                type=NotificationType.NEW_MESSAGE,
                title=f"New message from {sender_name}",
                body=body[:140],
                payload={
                    "conversation_id": str(context.conversation.id),
                    "match_id": str(context.match.id),
                    "sender_id": str(context.me),
                },
            )
        )

    return message, True


async def _wants_message_notification(db: AsyncSession, user_id: uuid.UUID) -> bool:
    pref = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
    ).scalar_one_or_none()
    return True if pref is None else pref.notify_new_message


async def mark_read(
    db: AsyncSession,
    context: ChatContext,
    up_to_message_id: uuid.UUID | None = None,
) -> int:
    """Advance the caller's read high-water mark. Returns remaining unread."""
    read_row = (
        await db.execute(
            select(MessageRead).where(
                MessageRead.conversation_id == context.conversation.id,
                MessageRead.user_id == context.me,
            )
        )
    ).scalar_one_or_none()

    if read_row is None:
        read_row = MessageRead(
            conversation_id=context.conversation.id, user_id=context.me
        )
        db.add(read_row)
        await db.flush()

    if up_to_message_id is None:
        read_row.unread_count = 0
        read_row.last_read_at = datetime.now(UTC)
        latest = (
            await db.execute(
                select(Message.id)
                .where(Message.conversation_id == context.conversation.id)
                .order_by(Message.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        read_row.last_read_message_id = latest
        return 0

    boundary = (
        await db.execute(
            select(Message).where(
                Message.id == up_to_message_id,
                Message.conversation_id == context.conversation.id,
            )
        )
    ).scalar_one_or_none()
    if boundary is None:
        raise ChatDenied("Message not found")

    remaining = (
        await db.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == context.conversation.id,
                Message.sender_id != context.me,
                Message.created_at > boundary.created_at,
            )
        )
    ).scalar_one()

    read_row.unread_count = int(remaining)
    read_row.last_read_at = datetime.now(UTC)
    read_row.last_read_message_id = boundary.id
    return int(remaining)
