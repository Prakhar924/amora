"""The matching engine.

Deterministic, rule-based: a match exists if and only if both users have a
`like` (or `superlike`) row pointing at each other. No scoring, no ML.

Concurrency
-----------
Two users can like each other at the same instant, in separate transactions.
Two distinct failures have to be prevented, and they pull in opposite
directions:

*Duplicate* matches — both transactions decide a match is warranted and each
inserts one. Prevented structurally: the pair is stored in canonical order
(`user_a_id < user_b_id`) under the UNIQUE constraint `uq_match_pair`, so both
transactions attempt a byte-identical row and `ON CONFLICT DO NOTHING` makes
the loser a no-op.

*Lost* matches — the subtler one. Under Postgres's default READ COMMITTED
isolation, neither transaction can see the other's uncommitted like, so both
probe for reciprocity, both find none, and two people who just liked each
other never match. `ON CONFLICT` cannot help here: no row is ever attempted.

The sequence below closes that window by taking a transaction-scoped advisory
lock keyed on the canonical pair *before* writing the like:

1. `pg_advisory_xact_lock(pair_key)` — serializes both directions of this one
   pair, and nothing else. Released automatically at commit or rollback.
2. Upsert the actor's own like (`ON CONFLICT ... DO UPDATE`), so a re-swipe
   updates rather than raising.
3. Probe for the reciprocal like — now guaranteed to see a committed like from
   the other side if there is one.
4. If present, insert the canonical match row with `ON CONFLICT DO NOTHING`
   and read it back.

The second transaction blocks at step 1 until the first commits, then sees its
like at step 3 and creates the match. Exactly one match, never zero.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Conversation,
    Like,
    LikeAction,
    Match,
    MessageRead,
    Notification,
    NotificationType,
    Profile,
    UserPreference,
)

# Actions that count as interest for the purposes of creating a match.
POSITIVE_ACTIONS = (LikeAction.LIKE, LikeAction.SUPERLIKE)

# Advisory lock keys share one global namespace, so this tag keeps match
# locking from colliding with any other advisory lock the system might take.
_LOCK_NAMESPACE = b"amora:match-pair:"


def pair_lock_key(first: uuid.UUID, second: uuid.UUID) -> int:
    """A stable signed 64-bit advisory-lock key for an unordered user pair.

    Order-independent: `(a, b)` and `(b, a)` produce the same key, which is
    what makes the two directions of a simultaneous mutual like serialize
    against each other.
    """
    user_a, user_b = Match.canonical_pair(first, second)
    digest = hashlib.blake2b(
        _LOCK_NAMESPACE + user_a.bytes + user_b.bytes, digest_size=8
    ).digest()
    # pg_advisory_xact_lock takes a signed bigint.
    return int.from_bytes(digest, "big", signed=True)


class SwipeOutcome:
    """Result of recording one swipe."""

    def __init__(
        self,
        action: LikeAction,
        was_update: bool,
        match: Match | None,
        newly_matched: bool,
    ) -> None:
        self.action = action
        self.was_update = was_update
        self.match = match
        self.newly_matched = newly_matched


async def register_like(
    db: AsyncSession,
    actor_id: uuid.UUID,
    target_id: uuid.UUID,
    action: LikeAction,
) -> SwipeOutcome:
    """Record a swipe and create the match if it is now mutual.

    The caller is responsible for authorization (block checks, target
    eligibility) and for committing the transaction.
    """
    if actor_id == target_id:
        raise ValueError("A user cannot swipe on themselves")

    # --- 1. Serialize this pair -----------------------------------------
    # Both directions hash to the same key, so the two racing transactions
    # queue behind each other and the reciprocity probe below cannot miss a
    # like that is still in flight. Scoped to the transaction, so it is
    # released on commit or rollback with no unlock path to forget.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": pair_lock_key(actor_id, target_id)},
    )

    # --- 2. Upsert the actor's decision --------------------------------
    stmt = (
        pg_insert(Like)
        .values(
            id=uuid.uuid4(),
            actor_id=actor_id,
            target_id=target_id,
            action=action.value,
        )
        .on_conflict_do_update(
            constraint="uq_like_actor_target",
            set_={"action": action.value, "updated_at": datetime.now(UTC)},
        )
        .returning(Like.created_at, Like.updated_at)
    )
    row = (await db.execute(stmt)).one()
    # If the row already existed, the upsert bumped updated_at past created_at.
    was_update = row.updated_at > row.created_at

    if action not in POSITIVE_ACTIONS:
        # A pass can retract an earlier like, which must dissolve the match.
        await _dissolve_match_if_any(db, actor_id, target_id)
        return SwipeOutcome(action, was_update, None, newly_matched=False)

    # --- 3. Is the interest reciprocated? -------------------------------
    reciprocal = (
        await db.execute(
            select(Like.id).where(
                Like.actor_id == target_id,
                Like.target_id == actor_id,
                Like.action.in_([a.value for a in POSITIVE_ACTIONS]),
            )
        )
    ).first()

    if reciprocal is None:
        await _notify_like(db, actor_id, target_id)
        return SwipeOutcome(action, was_update, None, newly_matched=False)

    # --- 4. Create the match (idempotently) ------------------------------
    user_a, user_b = Match.canonical_pair(actor_id, target_id)
    match_stmt = (
        pg_insert(Match)
        .values(id=uuid.uuid4(), user_a_id=user_a, user_b_id=user_b)
        .on_conflict_do_nothing(constraint="uq_match_pair")
        .returning(Match.id)
    )
    inserted = (await db.execute(match_stmt)).first()

    # Re-read unconditionally: on the losing side of a race `inserted` is None
    # but a match absolutely does exist, created by the other transaction.
    match = (
        await db.execute(
            select(Match).where(Match.user_a_id == user_a, Match.user_b_id == user_b)
        )
    ).scalar_one()

    newly_matched = inserted is not None

    if match.unmatched_at is not None and not newly_matched:
        # The pair matched before and someone unmatched. Re-liking revives the
        # existing row rather than inserting a second one.
        match.unmatched_at = None
        match.unmatched_by_id = None
        newly_matched = True

    if newly_matched:
        await _ensure_conversation(db, match)
        await _notify_match(db, match)

    return SwipeOutcome(action, was_update, match, newly_matched)


async def _ensure_conversation(db: AsyncSession, match: Match) -> Conversation:
    """Create the thread and both read-state rows for a match, idempotently."""
    existing = (
        await db.execute(select(Conversation).where(Conversation.match_id == match.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    conversation_id = uuid.uuid4()
    await db.execute(
        pg_insert(Conversation)
        .values(id=conversation_id, match_id=match.id)
        .on_conflict_do_nothing(index_elements=["match_id"])
    )
    conversation = (
        await db.execute(select(Conversation).where(Conversation.match_id == match.id))
    ).scalar_one()

    for participant in (match.user_a_id, match.user_b_id):
        await db.execute(
            pg_insert(MessageRead)
            .values(
                id=uuid.uuid4(),
                conversation_id=conversation.id,
                user_id=participant,
                unread_count=0,
            )
            .on_conflict_do_nothing(constraint="uq_read_conversation_user")
        )
    return conversation


async def _dissolve_match_if_any(
    db: AsyncSession, actor_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    user_a, user_b = Match.canonical_pair(actor_id, target_id)
    match = (
        await db.execute(
            select(Match).where(
                Match.user_a_id == user_a,
                Match.user_b_id == user_b,
                Match.unmatched_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if match is not None:
        match.unmatched_at = datetime.now(UTC)
        match.unmatched_by_id = actor_id


async def _display_name(db: AsyncSession, user_id: uuid.UUID) -> str:
    name = (
        await db.execute(select(Profile.display_name).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    return name or "Someone"


async def _wants(db: AsyncSession, user_id: uuid.UUID, field: str) -> bool:
    """Respect the recipient's notification opt-outs."""
    pref = (
        await db.execute(select(UserPreference).where(UserPreference.user_id == user_id))
    ).scalar_one_or_none()
    if pref is None:
        return True
    return bool(getattr(pref, field, True))


async def _notify_match(db: AsyncSession, match: Match) -> None:
    """Notify both sides. Written in the match transaction so it cannot be lost."""
    conversation = (
        await db.execute(select(Conversation).where(Conversation.match_id == match.id))
    ).scalar_one_or_none()

    for me, them in (
        (match.user_a_id, match.user_b_id),
        (match.user_b_id, match.user_a_id),
    ):
        if not await _wants(db, me, "notify_new_match"):
            continue
        their_name = await _display_name(db, them)
        db.add(
            Notification(
                user_id=me,
                type=NotificationType.NEW_MATCH,
                title="It's a match!",
                body=f"You and {their_name} liked each other.",
                payload={
                    "match_id": str(match.id),
                    "conversation_id": str(conversation.id) if conversation else None,
                    "other_user_id": str(them),
                },
            )
        )


async def _notify_like(
    db: AsyncSession, actor_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    """Tell the target someone liked them, without revealing who.

    Naming the liker would leak a one-sided like, which the product never
    exposes outside a mutual match.
    """
    if not await _wants(db, target_id, "notify_new_like"):
        return
    db.add(
        Notification(
            user_id=target_id,
            type=NotificationType.NEW_LIKE,
            title="Someone liked you",
            body="Keep swiping to find out who.",
            payload={},
        )
    )


async def unmatch(
    db: AsyncSession, match_id: uuid.UUID, actor_id: uuid.UUID
) -> Match | None:
    """Soft-unmatch and retract the actor's like so the pair cannot re-match.

    Retracting the like is what stops an unmatch from being undone by the
    other person swiping again.
    """
    match = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one_or_none()
    if match is None:
        return None
    if actor_id not in (match.user_a_id, match.user_b_id):
        return None
    if match.unmatched_at is None:
        match.unmatched_at = datetime.now(UTC)
        match.unmatched_by_id = actor_id

    other_id = match.other_user_id(actor_id)
    await db.execute(
        pg_insert(Like)
        .values(
            id=uuid.uuid4(),
            actor_id=actor_id,
            target_id=other_id,
            action=LikeAction.PASS.value,
        )
        .on_conflict_do_update(
            constraint="uq_like_actor_target",
            set_={"action": LikeAction.PASS.value, "updated_at": datetime.now(UTC)},
        )
    )
    return match
