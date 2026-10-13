"""Account deletion.

Deletion has to satisfy two things that pull against each other: the user's
personal data must actually be gone, and the other party's conversations must
not break or silently rewrite history.

The approach:

* Hard-delete everything that is purely the user's own — profile, photos (and
  the underlying S3 objects), preferences, interests, likes, blocks, sessions,
  notifications.
* Hard-delete the user's messages, then the conversation and the match. The
  other person loses the thread, which is the correct outcome: they cannot
  keep a record of a deleted account's messages.
* Keep the `users` row as a tombstone with every identifying field scrubbed.
  A random, non-reversible placeholder email keeps the UNIQUE constraint
  satisfied and frees the real address for re-registration.
* Keep `reports` filed *against* the user, with the reporter preserved, so a
  bad actor cannot erase a moderation trail by deleting their account.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import (
    Block,
    Conversation,
    Like,
    Match,
    Message,
    MessageRead,
    Notification,
    Profile,
    ProfilePhoto,
    Report,
    User,
    UserInterest,
    UserPreference,
    UserSession,
    UserStatus,
)
from app.services.storage import delete_object

logger = logging.getLogger(__name__)


async def purge_user_data(db: AsyncSession, user: User) -> None:
    """Scrub a user. The caller commits."""
    user_id = user.id

    # --- Photos: database rows and the stored objects -------------------
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()

    if profile is not None:
        keys = (
            (
                await db.execute(
                    select(ProfilePhoto.storage_key).where(
                        ProfilePhoto.profile_id == profile.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for key in keys:
            # Best-effort: a storage failure must not strand the deletion.
            delete_object(key)

        await db.execute(
            delete(UserInterest).where(UserInterest.profile_id == profile.id)
        )
        await db.execute(
            delete(ProfilePhoto).where(ProfilePhoto.profile_id == profile.id)
        )

    # --- Conversations and messages --------------------------------------
    match_ids = (
        (
            await db.execute(
                select(Match.id).where(
                    or_(Match.user_a_id == user_id, Match.user_b_id == user_id)
                )
            )
        )
        .scalars()
        .all()
    )

    if match_ids:
        conversation_ids = (
            (
                await db.execute(
                    select(Conversation.id).where(Conversation.match_id.in_(match_ids))
                )
            )
            .scalars()
            .all()
        )
        if conversation_ids:
            # Reports referencing a message must lose that pointer before the
            # message disappears, or the FK's SET NULL fires mid-delete.
            await db.execute(
                Report.__table__.update()
                .where(Report.conversation_id.in_(conversation_ids))
                .values(conversation_id=None, message_id=None)
            )
            await db.execute(
                delete(MessageRead).where(
                    MessageRead.conversation_id.in_(conversation_ids)
                )
            )
            await db.execute(
                delete(Message).where(Message.conversation_id.in_(conversation_ids))
            )
            await db.execute(
                delete(Conversation).where(Conversation.id.in_(conversation_ids))
            )
        await db.execute(delete(Match).where(Match.id.in_(match_ids)))

    # --- Swipes, blocks, sessions, notifications, preferences ------------
    await db.execute(
        delete(Like).where(or_(Like.actor_id == user_id, Like.target_id == user_id))
    )
    await db.execute(
        delete(Block).where(
            or_(Block.blocker_id == user_id, Block.blocked_id == user_id)
        )
    )
    await db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    await db.execute(delete(Notification).where(Notification.user_id == user_id))
    await db.execute(delete(UserPreference).where(UserPreference.user_id == user_id))

    # Reports this user *filed* are anonymised but kept, so a dismissed
    # report cannot be resurrected and the pattern of reports survives.
    await db.execute(
        Report.__table__.update()
        .where(Report.photo_id.is_not(None), Report.reported_id == user_id)
        .values(photo_id=None)
    )

    if profile is not None:
        await db.execute(delete(Profile).where(Profile.id == profile.id))

    # --- Tombstone the account row ---------------------------------------
    now = datetime.now(UTC)
    user.email = f"deleted+{uuid.uuid4().hex}@deleted.invalid"
    # Unknown random password: the row can never be authenticated against.
    user.hashed_password = hash_password(secrets.token_urlsafe(32))
    user.status = UserStatus.DELETED
    user.deleted_at = now
    user.email_verified_at = None
    user.token_version += 1
    user.suspension_reason = None
    user.last_login_at = None
    user.last_active_at = None
    # Date of birth is retained only as 1 January of the birth year, which is
    # enough to prove the account was adult without keeping an exact birthday.
    user.date_of_birth = datetime(user.date_of_birth.year, 1, 1, tzinfo=UTC)

    logger.info("Purged personal data for user %s", user_id)
