"""Match list and unmatching."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import Db, OnboardedUser
from app.models import (
    Block,
    Match,
    MessageRead,
    Profile,
    User,
    UserInterest,
    UserStatus,
)
from app.schemas.social import MatchList, MatchOut
from app.services.matching import unmatch as unmatch_service
from app.services.serializers import public_profile
from app.ws.manager import manager

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=MatchList, summary="List my active matches")
async def list_matches(
    user: OnboardedUser,
    db: Db,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> MatchList:
    """Active matches, most recent conversation first.

    Matches whose other party has deleted or been deactivated are filtered
    out, as are any that a block now covers.
    """
    base_filter = (
        or_(Match.user_a_id == user.id, Match.user_b_id == user.id),
        Match.unmatched_at.is_(None),
    )

    total = (
        await db.execute(select(func.count(Match.id)).where(*base_filter))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(Match)
                .where(*base_filter)
                .options(selectinload(Match.conversation))
                .order_by(Match.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        return MatchList(matches=[], total=0)

    other_ids = [m.other_user_id(user.id) for m in rows]

    # Hide matches with accounts that are gone or now blocked.
    valid_user_ids = set(
        (
            await db.execute(
                select(User.id).where(
                    User.id.in_(other_ids),
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    blocked_ids = set(
        (
            await db.execute(
                select(Block.blocked_id).where(
                    Block.blocker_id == user.id, Block.blocked_id.in_(other_ids)
                )
            )
        )
        .scalars()
        .all()
    ) | set(
        (
            await db.execute(
                select(Block.blocker_id).where(
                    Block.blocked_id == user.id, Block.blocker_id.in_(other_ids)
                )
            )
        )
        .scalars()
        .all()
    )

    visible_ids = [i for i in other_ids if i in valid_user_ids and i not in blocked_ids]

    profiles = {
        p.user_id: p
        for p in (
            (
                await db.execute(
                    select(Profile)
                    .where(Profile.user_id.in_(visible_ids))
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

    conversation_ids = [m.conversation.id for m in rows if m.conversation is not None]
    unread = {
        row.conversation_id: row.unread_count
        for row in (
            await db.execute(
                select(MessageRead).where(
                    MessageRead.user_id == user.id,
                    MessageRead.conversation_id.in_(conversation_ids or [uuid.uuid4()]),
                )
            )
        )
        .scalars()
        .all()
    }

    out: list[MatchOut] = []
    for match in rows:
        other_id = match.other_user_id(user.id)
        profile = profiles.get(other_id)
        if profile is None:
            continue
        conversation = match.conversation
        out.append(
            MatchOut(
                id=match.id,
                conversation_id=conversation.id if conversation else None,
                matched_at=match.created_at,
                profile=public_profile(profile),
                unread_count=unread.get(conversation.id, 0) if conversation else 0,
                last_message_at=conversation.last_message_at if conversation else None,
                last_message_preview=(
                    conversation.last_message_preview if conversation else None
                ),
            )
        )

    # Newest activity first: an active chat should outrank an older match.
    out.sort(key=lambda m: (m.last_message_at or m.matched_at), reverse=True)
    return MatchList(matches=out, total=total)


@router.delete(
    "/{match_id}",
    summary="Unmatch",
    description=(
        "Soft-unmatches and retracts your like, so the pair cannot re-match "
        "unless you deliberately like them again."
    ),
)
async def unmatch(match_id: uuid.UUID, user: OnboardedUser, db: Db) -> dict:
    match = await unmatch_service(db, match_id, user.id)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Match not found")

    await db.commit()

    # Let the other side's open client drop the thread immediately.
    await manager.send_to_user(
        match.other_user_id(user.id),
        {"type": "match.removed", "match_id": str(match.id)},
    )
    return {"message": "Unmatched."}
