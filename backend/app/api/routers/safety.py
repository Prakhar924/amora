"""Trust and safety: blocking, reporting, privacy settings, notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.api.deps import ActiveUser, Db
from app.core.config import settings
from app.core.ratelimit import enforce_user_limit
from app.models import (
    Block,
    Conversation,
    Like,
    LikeAction,
    Match,
    Message,
    Notification,
    Profile,
    Report,
    ReportStatus,
    User,
)
from app.schemas.social import (
    BlockedUserOut,
    BlockRequest,
    NotificationList,
    NotificationOut,
    ReportOut,
    ReportRequest,
)
from app.ws.manager import manager

router = APIRouter(tags=["safety"])


@router.post(
    "/safety/block",
    status_code=status.HTTP_201_CREATED,
    summary="Block a user",
)
async def block_user(payload: BlockRequest, user: ActiveUser, db: Db) -> dict:
    """Block someone.

    Blocking also unmatches and records a mutual pass, so the pair can never
    reappear in either discovery feed. The block itself is enforced in both
    directions everywhere it is checked.
    """
    if payload.user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot block yourself.")

    target = (
        await db.execute(select(User.id).where(User.id == payload.user_id))
    ).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    await db.execute(
        pg_insert(Block)
        .values(
            id=uuid.uuid4(),
            blocker_id=user.id,
            blocked_id=payload.user_id,
            reason=payload.reason,
        )
        .on_conflict_do_nothing(constraint="uq_block_pair")
    )

    # Dissolve any match between the two.
    pair_a, pair_b = Match.canonical_pair(user.id, payload.user_id)
    match = (
        await db.execute(
            select(Match).where(
                Match.user_a_id == pair_a,
                Match.user_b_id == pair_b,
                Match.unmatched_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if match is not None:
        match.unmatched_at = datetime.now(UTC)
        match.unmatched_by_id = user.id

    # Record a pass in both directions so neither side is recommended again.
    for actor, other in ((user.id, payload.user_id), (payload.user_id, user.id)):
        await db.execute(
            pg_insert(Like)
            .values(
                id=uuid.uuid4(),
                actor_id=actor,
                target_id=other,
                action=LikeAction.PASS.value,
            )
            .on_conflict_do_update(
                constraint="uq_like_actor_target",
                set_={"action": LikeAction.PASS.value, "updated_at": datetime.now(UTC)},
            )
        )

    await db.commit()

    if match is not None:
        await manager.send_to_user(
            payload.user_id, {"type": "match.removed", "match_id": str(match.id)}
        )

    return {"message": "User blocked. They can no longer see you or contact you."}


@router.delete("/safety/block/{user_id}", summary="Unblock a user")
async def unblock_user(user_id: uuid.UUID, user: ActiveUser, db: Db) -> dict:
    result = await db.execute(
        Block.__table__.delete().where(
            Block.blocker_id == user.id, Block.blocked_id == user_id
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That user is not blocked.")
    # The mutual passes stay in place: unblocking should not silently put
    # someone back into the other person's deck.
    return {"message": "User unblocked."}


@router.get(
    "/safety/blocks",
    response_model=list[BlockedUserOut],
    summary="List users I have blocked",
)
async def list_blocks(user: ActiveUser, db: Db) -> list[BlockedUserOut]:
    rows = (
        await db.execute(
            select(Block, Profile.display_name)
            .outerjoin(Profile, Profile.user_id == Block.blocked_id)
            .where(Block.blocker_id == user.id)
            .order_by(Block.created_at.desc())
        )
    ).all()
    return [
        BlockedUserOut(
            user_id=block.blocked_id,
            display_name=name,
            blocked_at=block.created_at,
        )
        for block, name in rows
    ]


@router.post(
    "/safety/report",
    response_model=ReportOut,
    status_code=status.HTTP_201_CREATED,
    summary="Report a user",
)
async def report_user(payload: ReportRequest, user: ActiveUser, db: Db) -> ReportOut:
    """File a report for admin review.

    Any referenced message or conversation is verified to be one the reporter
    actually participates in, so a report cannot be used to make arbitrary
    private messages visible to moderators.
    """
    await enforce_user_limit("report", user.id, settings.rate_limit_report)

    if payload.user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot report yourself.")

    target = (
        await db.execute(select(User.id).where(User.id == payload.user_id))
    ).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    conversation_id = payload.conversation_id
    message_id = payload.message_id

    if message_id is not None:
        row = (
            await db.execute(
                select(Message, Match)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .join(Match, Match.id == Conversation.match_id)
                .where(Message.id == message_id)
            )
        ).first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
        message, match = row
        if user.id not in (match.user_a_id, match.user_b_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
        conversation_id = message.conversation_id

    elif conversation_id is not None:
        row = (
            await db.execute(
                select(Match)
                .join(Conversation, Conversation.match_id == Match.id)
                .where(Conversation.id == conversation_id)
            )
        ).scalar_one_or_none()
        if row is None or user.id not in (row.user_a_id, row.user_b_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    report = Report(
        reporter_id=user.id,
        reported_id=payload.user_id,
        reason=payload.reason,
        details=payload.details,
        conversation_id=conversation_id,
        message_id=message_id,
        photo_id=payload.photo_id,
        status=ReportStatus.OPEN,
    )
    db.add(report)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "That report could not be filed."
        ) from exc

    await db.refresh(report)
    return ReportOut.model_validate(report)


@router.get(
    "/safety/reports",
    response_model=list[ReportOut],
    summary="List reports I have filed",
)
async def list_my_reports(user: ActiveUser, db: Db) -> list[ReportOut]:
    rows = (
        (
            await db.execute(
                select(Report)
                .where(Report.reporter_id == user.id)
                .order_by(Report.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [ReportOut.model_validate(r) for r in rows]


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
@router.get(
    "/notifications",
    response_model=NotificationList,
    summary="My notification feed",
)
async def list_notifications(
    user: ActiveUser,
    db: Db,
    limit: int = Query(default=30, ge=1, le=100),
    unread_only: bool = Query(default=False),
) -> NotificationList:
    conditions = [Notification.user_id == user.id]
    if unread_only:
        conditions.append(Notification.read_at.is_(None))

    rows = (
        (
            await db.execute(
                select(Notification)
                .where(*conditions)
                .order_by(Notification.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    unread = (
        await db.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.read_at.is_(None)
            )
        )
    ).scalar_one()

    return NotificationList(
        notifications=[NotificationOut.model_validate(n) for n in rows],
        unread_count=int(unread),
    )


@router.post("/notifications/read", summary="Mark notifications as read")
async def mark_notifications_read(
    user: ActiveUser, db: Db, notification_id: uuid.UUID | None = None
) -> dict:
    conditions = [Notification.user_id == user.id, Notification.read_at.is_(None)]
    if notification_id is not None:
        conditions.append(Notification.id == notification_id)

    await db.execute(
        Notification.__table__.update()
        .where(*conditions)
        .values(read_at=datetime.now(UTC))
    )
    await db.commit()
    return {"message": "Marked as read."}
