"""The chat WebSocket.

Protocol (JSON frames, `type` discriminates):

Client -> server
  {"type":"ping"}
  {"type":"message.send","conversation_id":"...","body":"...","client_nonce":"..."}
  {"type":"typing","conversation_id":"...","is_typing":true}
  {"type":"message.read","conversation_id":"...","up_to_message_id":"..."}

Server -> client
  {"type":"ready","user_id":"..."}
  {"type":"pong"}
  {"type":"message.created","message":{...}}
  {"type":"message.read","conversation_id":"...","by_user_id":"..."}
  {"type":"typing","conversation_id":"...","user_id":"...","is_typing":true}
  {"type":"match.created","match":{...}}
  {"type":"match.removed","match_id":"..."}
  {"type":"error","detail":"...","code":"..."}

Authentication is a `token` query parameter, because browsers cannot set
headers on a WebSocket handshake. Every inbound frame is re-authorized through
`app.services.chat.authorize_conversation` — the socket being open is never
treated as standing permission to post into a given thread.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.ratelimit import Limit, consume
from app.core.security import TokenError, decode_token
from app.db.session import SessionLocal
from app.models import User, UserStatus
from app.schemas.social import MessageOut
from app.services.chat import (
    ChatDenied,
    authorize_conversation,
    mark_read,
    persist_message,
)
from app.ws.manager import manager

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_BODY_LENGTH = 4000


async def _authenticate(token: str) -> User | None:
    """Resolve the socket's user, applying the same checks as the REST deps."""
    try:
        payload = decode_token(token, "access")
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, KeyError, ValueError):
        return None

    async with SessionLocal() as db:
        user = (
            await db.execute(
                select(User)
                .where(User.id == user_id)
                .options(selectinload(User.profile))
            )
        ).scalar_one_or_none()

        if (
            user is None
            or user.deleted_at is not None
            or user.status != UserStatus.ACTIVE
            or payload.get("tv") != user.token_version
        ):
            return None
        # Chat requires a finished profile, exactly as the REST routes do.
        if user.profile is None or user.profile.completed_at is None:
            return None
        return user


@router.websocket("/ws/chat")
async def chat_socket(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
) -> None:
    user = await _authenticate(token)
    if user is None:
        # 1008 = policy violation. The client treats this as "re-auth needed"
        # and refreshes its access token rather than retrying blindly.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(user.id, websocket)
    await websocket.send_json({"type": "ready", "user_id": str(user.id)})

    try:
        while True:
            frame = await websocket.receive_json()
            await _dispatch(websocket, user.id, frame)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.info("Socket for %s closed unexpectedly: %s", user.id, exc)
    finally:
        await manager.disconnect(user.id, websocket)


async def _error(websocket: WebSocket, detail: str, code: str = "bad_request") -> None:
    await websocket.send_json({"type": "error", "detail": detail, "code": code})


async def _dispatch(websocket: WebSocket, user_id: uuid.UUID, frame: dict) -> None:
    kind = frame.get("type")

    if kind == "ping":
        await websocket.send_json({"type": "pong"})
        return

    if kind == "message.send":
        await _handle_send(websocket, user_id, frame)
        return

    if kind == "typing":
        await _handle_typing(websocket, user_id, frame)
        return

    if kind == "message.read":
        await _handle_read(websocket, user_id, frame)
        return

    await _error(websocket, f"Unknown frame type: {kind!r}", "unknown_type")


def _parse_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def _handle_send(websocket: WebSocket, user_id: uuid.UUID, frame: dict) -> None:
    conversation_id = _parse_uuid(frame.get("conversation_id"))
    body = (frame.get("body") or "").strip()
    nonce = frame.get("client_nonce")

    if conversation_id is None:
        await _error(websocket, "A valid conversation_id is required")
        return
    if not body:
        await _error(websocket, "Message cannot be empty")
        return
    if len(body) > MAX_BODY_LENGTH:
        await _error(
            websocket, f"Message cannot exceed {MAX_BODY_LENGTH} characters"
        )
        return

    # The socket is subject to the same spam budget as the REST path.
    allowed, retry_after = await consume(
        "message", str(user_id), Limit.parse(settings.rate_limit_message)
    )
    if not allowed:
        await websocket.send_json(
            {
                "type": "error",
                "code": "rate_limited",
                "detail": "You are sending messages too quickly.",
                "retry_after": retry_after,
            }
        )
        return

    async with SessionLocal() as db:
        try:
            context = await authorize_conversation(
                db, conversation_id, user_id, for_sending=True
            )
            message, created = await persist_message(
                db, context, body, nonce if isinstance(nonce, str) else None
            )
            await db.commit()
            await db.refresh(message)
        except ChatDenied as exc:
            await _error(
                websocket,
                exc.reason,
                "not_found" if exc.not_found else "forbidden",
            )
            return

        out = MessageOut.model_validate(message)

    envelope = {"type": "message.created", "message": out.model_dump(mode="json")}
    if created:
        await manager.send_to_user(context.other_user_id, envelope)
    # Always echo to the sender, including on a deduplicated retry, so the
    # client can reconcile its optimistic bubble either way.
    await manager.send_to_user(user_id, envelope)


async def _handle_typing(websocket: WebSocket, user_id: uuid.UUID, frame: dict) -> None:
    conversation_id = _parse_uuid(frame.get("conversation_id"))
    if conversation_id is None:
        await _error(websocket, "A valid conversation_id is required")
        return

    is_typing = bool(frame.get("is_typing", True))

    async with SessionLocal() as db:
        try:
            context = await authorize_conversation(
                db, conversation_id, user_id, for_sending=True
            )
        except ChatDenied:
            # Stay quiet: a typing indicator is not worth an error frame, and
            # silence avoids confirming whether the thread exists.
            return

    # Ephemeral, never persisted.
    await manager.send_to_user(
        context.other_user_id,
        {
            "type": "typing",
            "conversation_id": str(conversation_id),
            "user_id": str(user_id),
            "is_typing": is_typing,
        },
    )


async def _handle_read(websocket: WebSocket, user_id: uuid.UUID, frame: dict) -> None:
    conversation_id = _parse_uuid(frame.get("conversation_id"))
    if conversation_id is None:
        await _error(websocket, "A valid conversation_id is required")
        return

    up_to = _parse_uuid(frame.get("up_to_message_id")) if frame.get(
        "up_to_message_id"
    ) else None

    async with SessionLocal() as db:
        try:
            context = await authorize_conversation(
                db, conversation_id, user_id, for_sending=False
            )
            remaining = await mark_read(db, context, up_to)
            await db.commit()
        except ChatDenied as exc:
            await _error(websocket, exc.reason, "not_found")
            return

    await manager.send_to_user(
        context.other_user_id,
        {
            "type": "message.read",
            "conversation_id": str(conversation_id),
            "by_user_id": str(user_id),
        },
    )
    await websocket.send_json(
        {
            "type": "message.read.ack",
            "conversation_id": str(conversation_id),
            "unread_count": remaining,
        }
    )
