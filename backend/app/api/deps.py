"""Shared FastAPI dependencies: authentication and authorization.

The ladder of dependencies is deliberate, so each endpoint can state exactly
how much standing it requires:

* `get_current_user`     — a valid token and a non-deleted account.
* `get_verified_user`    — additionally, a confirmed email address.
* `get_active_user`      — additionally, active (not suspended) status. Most
                           product endpoints use this.
* `get_onboarded_user`   — additionally, a completed profile. Required to
                           swipe, match, or chat.
* `get_admin_user`       — moderation endpoints only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import TokenError, decode_token
from app.db.session import get_db
from app.models import Profile, User, UserStatus

# auto_error=False lets us return a consistent error body instead of the
# bare 403 that HTTPBearer raises by default for a missing header.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or not credentials.credentials:
        raise CREDENTIALS_ERROR

    try:
        payload = decode_token(credentials.credentials, "access")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise CREDENTIALS_ERROR from exc

    user = (
        await db.execute(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.profile).selectinload(Profile.photos))
        )
    ).scalar_one_or_none()

    if user is None or user.deleted_at is not None:
        raise CREDENTIALS_ERROR

    # A password change or "log out everywhere" bumps `token_version`, which
    # retires every access token issued before it without a blocklist.
    if payload.get("tv") != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Lets the rate limiter key on the account rather than the IP.
    request.state.user_id = str(user.id)
    return user


async def get_verified_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    # Deployments that cannot send mail to arbitrary addresses switch this
    # off; every other gate in the ladder still applies.
    if not settings.require_email_verification:
        return user
    if user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please confirm your email address to continue.",
        )
    return user


async def get_active_user(
    user: Annotated[User, Depends(get_verified_user)],
    db: AsyncSession = Depends(get_db),
) -> User:
    now = datetime.now(UTC)

    # A suspension that has elapsed reinstates the account on next use, so a
    # timed suspension does not need a background job to lift it.
    if (
        user.status == UserStatus.SUSPENDED
        and user.suspended_until is not None
        and user.suspended_until <= now
    ):
        user.status = UserStatus.ACTIVE
        user.suspended_until = None
        user.suspension_reason = None
        await db.commit()

    if user.status == UserStatus.SUSPENDED:
        detail = "Your account is suspended."
        if user.suspension_reason:
            detail += f" Reason: {user.suspension_reason}"
        if user.suspended_until:
            detail += f" It will be reinstated on {user.suspended_until:%Y-%m-%d}."
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    if user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not active.",
        )

    # Powers the "recently active" ordering in discovery. Throttled to one
    # write per 5 minutes so a swipe session is not one UPDATE per request.
    if user.last_active_at is None or (now - user.last_active_at).total_seconds() > 300:
        user.last_active_at = now
        await db.commit()

    return user


async def get_onboarded_user(
    user: Annotated[User, Depends(get_active_user)],
) -> User:
    """Require a finished profile before letting a user into the dating loop.

    Without this, an empty profile could swipe on everyone while showing a
    blank card in return.
    """
    if user.profile is None or user.profile.completed_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Finish setting up your profile — add a name, your gender and "
                "at least one photo — before you start matching."
            ),
        )
    return user


async def get_admin_user(
    user: Annotated[User, Depends(get_active_user)],
) -> User:
    if not user.is_admin:
        # 404 rather than 403: do not confirm that an admin surface exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
VerifiedUser = Annotated[User, Depends(get_verified_user)]
ActiveUser = Annotated[User, Depends(get_active_user)]
OnboardedUser = Annotated[User, Depends(get_onboarded_user)]
AdminUser = Annotated[User, Depends(get_admin_user)]
Db = Annotated[AsyncSession, Depends(get_db)]
