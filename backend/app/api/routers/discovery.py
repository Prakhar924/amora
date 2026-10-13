"""Discovery feed and swipe actions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import Db, OnboardedUser
from app.core.config import settings
from app.core.ratelimit import enforce_user_limit
from app.models import (
    Block,
    Conversation,
    Profile,
    User,
    UserInterest,
    UserStatus,
)
from app.schemas.social import DiscoveryFeed, MatchOut, SwipeRequest, SwipeResult
from app.services import matching
from app.services.discovery import encode_cursor, fetch_candidates
from app.services.serializers import public_profile
from app.ws.manager import manager

router = APIRouter(tags=["discovery"])


@router.get(
    "/discovery",
    response_model=DiscoveryFeed,
    summary="Get the next page of recommended profiles",
)
async def get_discovery_feed(
    user: OnboardedUser,
    db: Db,
    limit: int = Query(
        default=settings.discovery_page_size,
        ge=1,
        le=50,
        description="Profiles per page",
    ),
    cursor: str | None = Query(default=None, description="Cursor from a prior page"),
) -> DiscoveryFeed:
    """The swipe deck.

    Every exclusion — already swiped, blocked, suspended, hidden, under 18,
    outside the viewer's filters — is applied in SQL. See
    `app.services.discovery` for the full ordering of those filters.
    """
    candidates, next_offset = await fetch_candidates(db, user, limit=limit, cursor=cursor)

    if not candidates:
        return DiscoveryFeed(profiles=[], next_cursor=None, exhausted=True)

    # Eager-load relations the serializer touches, to avoid N+1 lazy loads.
    profile_ids = [p.id for p, _ in candidates]
    hydrated = {
        p.id: p
        for p in (
            (
                await db.execute(
                    select(Profile)
                    .where(Profile.id.in_(profile_ids))
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

    return DiscoveryFeed(
        profiles=[
            public_profile(hydrated[profile.id], distance)
            for profile, distance in candidates
            if profile.id in hydrated
        ],
        next_cursor=encode_cursor(next_offset),
        exhausted=len(candidates) < limit,
    )


@router.post(
    "/discovery/swipe",
    response_model=SwipeResult,
    summary="Like, superlike, or pass on a profile",
)
async def swipe(payload: SwipeRequest, user: OnboardedUser, db: Db) -> SwipeResult:
    """Record a swipe, creating a match when the interest is mutual.

    Authorization performed here before delegating to the matching engine:
    the target must exist, be active, be eligible, and not be blocked in
    either direction.
    """
    await enforce_user_limit("swipe", user.id, settings.rate_limit_swipe)

    if payload.target_user_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot swipe on yourself.")

    target = (
        await db.execute(
            select(User)
            .where(
                User.id == payload.target_user_id,
                User.status == UserStatus.ACTIVE,
                User.deleted_at.is_(None),
            )
            .options(selectinload(User.profile))
        )
    ).scalar_one_or_none()

    if target is None or target.profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That profile is not available.")

    blocked = (
        await db.execute(
            select(Block.id).where(
                or_(
                    (Block.blocker_id == user.id)
                    & (Block.blocked_id == payload.target_user_id),
                    (Block.blocker_id == payload.target_user_id)
                    & (Block.blocked_id == user.id),
                )
            )
        )
    ).first()
    if blocked is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That profile is not available.")

    try:
        outcome = await matching.register_like(
            db, user.id, payload.target_user_id, payload.action
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await db.commit()

    result = SwipeResult(
        recorded=True,
        action=outcome.action,
        is_match=outcome.newly_matched,
        was_update=outcome.was_update,
    )

    if outcome.newly_matched and outcome.match is not None:
        match_out = await _build_match_out(db, outcome.match, user.id)
        result.match = match_out

        # Push the match to the other user if they have a socket open. The
        # durable Notification row was already written in the match
        # transaction, so a missed push is not a lost notification.
        other_id = outcome.match.other_user_id(user.id)
        their_view = await _build_match_out(db, outcome.match, other_id)
        await manager.send_to_user(
            other_id,
            {
                "type": "match.created",
                "match": their_view.model_dump(mode="json"),
            },
        )

    return result


async def _build_match_out(db: Db, match, viewer_id: uuid.UUID) -> MatchOut:
    """Render a match from one participant's point of view."""
    other_id = match.other_user_id(viewer_id)

    other_profile = (
        await db.execute(
            select(Profile)
            .where(Profile.user_id == other_id)
            .options(
                selectinload(Profile.photos),
                selectinload(Profile.interests).selectinload(UserInterest.interest),
                selectinload(Profile.user),
            )
        )
    ).scalar_one()

    conversation = (
        await db.execute(select(Conversation).where(Conversation.match_id == match.id))
    ).scalar_one_or_none()

    return MatchOut(
        id=match.id,
        conversation_id=conversation.id if conversation else None,
        matched_at=match.created_at,
        profile=public_profile(other_profile),
        unread_count=0,
        last_message_at=conversation.last_message_at if conversation else None,
        last_message_preview=conversation.last_message_preview if conversation else None,
    )
