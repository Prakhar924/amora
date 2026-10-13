"""Conversations, message history, and sending over REST."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import Db, OnboardedUser
from app.core.config import settings
from app.core.ratelimit import enforce_user_limit
from app.models import (
    Block,
    Conversation,
    Match,
    Message,
    MessageRead,
    Profile,
    User,
    UserInterest,
    UserStatus,
)
from app.schemas.social import (
    ConversationList,
    ConversationOut,
    MarkReadRequest,
    MessageOut,
    MessagePage,
    SendMessageRequest,
)
from app.services.chat import (
    ChatDenied,
    authorize_conversation,
    mark_read,
    persist_message,
)
from app.services.serializers import public_profile
from app.ws.manager import manager

router = APIRouter(tags=["messaging"])


def _deny(exc: ChatDenied) -> HTTPException:
    return HTTPException(
        status.HTTP_404_NOT_FOUND if exc.not_found else status.HTTP_403_FORBIDDEN,
        exc.reason,
    )


def _encode_cursor(moment: datetime) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"before": moment.isoformat()}).encode()
    ).decode()


def _decode_cursor(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return datetime.fromisoformat(payload["before"])
    except (ValueError, KeyError, TypeError):
        return None


@router.get(
    "/conversations",
    response_model=ConversationList,
    summary="List my conversations (the chat list)",
)
async def list_conversations(user: OnboardedUser, db: Db) -> ConversationList:
    rows = (
        (
            await db.execute(
                select(Conversation, Match)
                .join(Match, Match.id == Conversation.match_id)
                .where(
                    or_(Match.user_a_id == user.id, Match.user_b_id == user.id),
                    Match.unmatched_at.is_(None),
                )
                .order_by(
                    Conversation.last_message_at.desc().nullslast(),
                    Conversation.created_at.desc(),
                )
            )
        )
        .all()
    )

    if not rows:
        return ConversationList(conversations=[], total_unread=0)

    other_ids = [match.other_user_id(user.id) for _, match in rows]

    profiles = {
        p.user_id: p
        for p in (
            (
                await db.execute(
                    select(Profile)
                    .join(User, User.id == Profile.user_id)
                    .where(
                        Profile.user_id.in_(other_ids),
                        User.status == UserStatus.ACTIVE,
                        User.deleted_at.is_(None),
                    )
                    .options(
                        selectinload(Profile.photos),
                        selectinload(Profile.interests).selectinload(
                            UserInterest.interest
                        ),
                        selectinload(Profile.user),
                    )
                )
            )
            .scalars()
            .all()
        )
    }

    blocked_ids = set(
        (
            await db.execute(
                select(Block.blocked_id).where(Block.blocker_id == user.id)
            )
        )
        .scalars()
        .all()
    ) | set(
        (
            await db.execute(
                select(Block.blocker_id).where(Block.blocked_id == user.id)
            )
        )
        .scalars()
        .all()
    )

    unread_rows = {
        row.conversation_id: row.unread_count
        for row in (
            await db.execute(
                select(MessageRead).where(MessageRead.user_id == user.id)
            )
        )
        .scalars()
        .all()
    }

    conversations: list[ConversationOut] = []
    total_unread = 0
    for conversation, match in rows:
        other_id = match.other_user_id(user.id)
        profile = profiles.get(other_id)
        # A conversation with a deleted account is dropped entirely.
        if profile is None:
            continue
        unread = unread_rows.get(conversation.id, 0)
        total_unread += unread
        conversations.append(
            ConversationOut(
                id=conversation.id,
                match_id=match.id,
                other_user=public_profile(profile),
                unread_count=unread,
                last_message_at=conversation.last_message_at,
                last_message_preview=conversation.last_message_preview,
                can_send=other_id not in blocked_ids,
            )
        )

    return ConversationList(conversations=conversations, total_unread=total_unread)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessagePage,
    summary="Get message history, newest first",
)
async def get_messages(
    conversation_id: uuid.UUID,
    user: OnboardedUser,
    db: Db,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(
        default=None, description="Cursor from a previous page, for older messages"
    ),
) -> MessagePage:
    try:
        context = await authorize_conversation(
            db, conversation_id, user.id, for_sending=False
        )
    except ChatDenied as exc:
        raise _deny(exc) from exc

    conditions = [
        Message.conversation_id == context.conversation.id,
        Message.deleted_at.is_(None),
    ]
    before = _decode_cursor(cursor)
    if before is not None:
        conditions.append(Message.created_at < before)

    rows = (
        (
            await db.execute(
                select(Message)
                .where(*conditions)
                .order_by(Message.created_at.desc())
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )

    has_more = len(rows) > limit
    page = rows[:limit]

    return MessagePage(
        messages=[MessageOut.model_validate(m) for m in page],
        next_cursor=_encode_cursor(page[-1].created_at) if has_more and page else None,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Send a message",
)
async def send_message(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    user: OnboardedUser,
    db: Db,
) -> MessageOut:
    """Send over REST.

    The WebSocket is the primary path, but this exists so a client with a
    broken socket can still send, and so the API is testable without a socket.
    """
    await enforce_user_limit("message", user.id, settings.rate_limit_message)

    try:
        context = await authorize_conversation(
            db, conversation_id, user.id, for_sending=True
        )
        message, created = await persist_message(
            db, context, payload.body, payload.client_nonce
        )
    except ChatDenied as exc:
        raise _deny(exc) from exc

    await db.commit()
    await db.refresh(message)

    out = MessageOut.model_validate(message)

    if created:
        envelope = {"type": "message.created", "message": out.model_dump(mode="json")}
        # To the recipient, and to the sender's own other devices.
        await manager.send_to_user(context.other_user_id, envelope)
        await manager.send_to_user(user.id, envelope)

    return out


@router.post(
    "/conversations/{conversation_id}/read",
    summary="Mark a conversation (or messages up to a point) as read",
)
async def mark_conversation_read(
    conversation_id: uuid.UUID,
    payload: MarkReadRequest,
    user: OnboardedUser,
    db: Db,
) -> dict:
    try:
        context = await authorize_conversation(
            db, conversation_id, user.id, for_sending=False
        )
        remaining = await mark_read(db, context, payload.up_to_message_id)
    except ChatDenied as exc:
        raise _deny(exc) from exc

    await db.commit()

    # Read receipt for the other side's open client.
    await manager.send_to_user(
        context.other_user_id,
        {
            "type": "message.read",
            "conversation_id": str(conversation_id),
            "by_user_id": str(user.id),
        },
    )
    return {"unread_count": remaining}


@router.get("/conversations/unread-count", summary="Total unread messages")
async def unread_count(user: OnboardedUser, db: Db) -> dict:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(MessageRead.unread_count), 0)).where(
                MessageRead.user_id == user.id
            )
        )
    ).scalar_one()
    return {"total_unread": int(total)}
