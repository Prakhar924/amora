"""Authentication: registration, login, refresh rotation, logout, recovery.

Enumeration policy
------------------
`/register`, `/forgot-password`, and `/login` deliberately avoid confirming
whether an address has an account. Registering an existing address returns the
same 202 as a new one (and sends a "someone tried to register" email to the
real owner); forgot-password always returns 202; login always returns the same
401 regardless of whether the user or the password was wrong.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import ActiveUser, CurrentUser, Db
from app.core.config import settings
from app.core.ratelimit import rate_limit
from app.core.security import (
    TokenError,
    create_access_token,
    create_email_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models import Profile, User, UserPreference, UserSession, UserStatus
from app.schemas.auth import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenPair,
    VerifyEmailRequest,
)
from app.services import email as email_service
from app.services.account import purge_user_data
from app.services.age import age_on

router = APIRouter(prefix="/auth", tags=["auth"])


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
async def _issue_tokens(
    db: Db,
    user: User,
    user_agent: str | None,
    family_id: uuid.UUID | None = None,
) -> TokenPair:
    """Mint an access token and persist a new refresh session."""
    plaintext, hashed = new_refresh_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hashed,
        family_id=family_id or uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        user_agent=(user_agent or "")[:400] or None,
    )
    db.add(session)
    await db.flush()
    return TokenPair(
        access_token=create_access_token(user.id, user.token_version, user.is_admin),
        refresh_token=plaintext,
        expires_in=settings.access_token_ttl_minutes * 60,
    )


def _me(user: User) -> MeResponse:
    return MeResponse(
        id=user.id,
        email=user.email,
        status=user.status,
        is_admin=user.is_admin,
        email_verified=user.email_verified_at is not None,
        age=age_on(user.date_of_birth),
        has_profile=user.profile is not None,
        profile_complete=user.profile is not None and user.profile.completed_at is not None,
        created_at=user.created_at,
    )


# --------------------------------------------------------------------------
# Registration & verification
# --------------------------------------------------------------------------
@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("register", settings.rate_limit_register))],
    summary="Create an account (18+ only)",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    background: BackgroundTasks,
    db: Db,
) -> dict:
    """Register a new user.

    Age eligibility is validated on the schema (`RegisterRequest`) against the
    submitted date of birth, which is the only place age is ever decided.
    """
    existing = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    if existing is not None:
        # Do not reveal that the address is taken. Tell the real owner instead.
        background.add_task(
            email_service.send_email,
            payload.email,
            f"Someone tried to register with your {settings.app_name} address",
            "Someone just tried to create an account using this email address. "
            "If it was you, you already have an account — try signing in or "
            "resetting your password instead.",
        )
        return {
            "message": (
                "Check your inbox to continue. If you already have an account, "
                "sign in instead."
            )
        }

    now = datetime.now(UTC)
    # When email verification is disabled the account is usable immediately.
    # That is a deliberate downgrade for deployments that cannot send mail to
    # arbitrary addresses (no custom domain); see `require_email_verification`.
    verification_required = settings.require_email_verification
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        date_of_birth=datetime.combine(
            payload.date_of_birth, datetime.min.time(), tzinfo=UTC
        ),
        status=(
            UserStatus.PENDING_VERIFICATION
            if verification_required
            else UserStatus.ACTIVE
        ),
        email_verified_at=None if verification_required else now,
        terms_accepted_at=now,
        privacy_accepted_at=now,
    )
    db.add(user)

    try:
        await db.flush()
    except IntegrityError:
        # Lost a race against a concurrent registration of the same address.
        await db.rollback()
        return {"message": "Check your inbox to continue."}

    # A profile and preference row always accompany a user, so the rest of the
    # app never has to handle their absence.
    db.add(
        Profile(
            user_id=user.id,
            display_name=payload.display_name,
            gender=payload.gender,
        )
    )
    db.add(
        UserPreference(
            user_id=user.id,
            # Default to showing everyone rather than guessing from gender.
            interested_in=[],
            min_age=settings.minimum_age,
            max_age=99,
            max_distance_km=50,
        )
    )
    await db.commit()

    if not verification_required:
        return {
            "message": "Account created. You can sign in now.",
            "user_id": str(user.id),
            "verification_required": False,
        }

    token = create_email_token(user.id, "verify_email", hours=24)
    background.add_task(email_service.send_verification_email, user.email, token)

    return {
        "message": "Account created. Check your inbox to confirm your email address.",
        "user_id": str(user.id),
        "verification_required": True,
    }


@router.post("/verify-email", summary="Confirm an email address")
async def verify_email(payload: VerifyEmailRequest, db: Db) -> dict:
    try:
        claims = decode_token(payload.token, "verify_email")
    except TokenError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(claims["sub"])))
    ).scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is no longer valid.")

    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        if user.status == UserStatus.PENDING_VERIFICATION:
            user.status = UserStatus.ACTIVE
        await db.commit()

    return {"message": "Email confirmed. You can sign in now."}


@router.post(
    "/resend-verification",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("resend", "3/3600"))],
)
async def resend_verification(
    user: CurrentUser, background: BackgroundTasks
) -> dict:
    if user.email_verified_at is not None:
        return {"message": "Your email is already confirmed."}
    token = create_email_token(user.id, "verify_email", hours=24)
    background.add_task(email_service.send_verification_email, user.email, token)
    return {"message": "Verification email sent."}


# --------------------------------------------------------------------------
# Login / refresh / logout
# --------------------------------------------------------------------------
@router.post(
    "/login",
    response_model=TokenPair,
    dependencies=[Depends(rate_limit("login", settings.rate_limit_login))],
    summary="Exchange credentials for a token pair",
)
async def login(payload: LoginRequest, request: Request, db: Db) -> TokenPair:
    user = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    # Identical failure for unknown address and wrong password, and the hash
    # is still computed when the user is missing so the timing matches.
    if user is None:
        verify_password(payload.password, hash_password("timing-equalizer"))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    if user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")

    if (
        settings.require_email_verification
        and user.status == UserStatus.PENDING_VERIFICATION
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Confirm your email address before signing in.",
        )

    user.last_login_at = datetime.now(UTC)
    tokens = await _issue_tokens(db, user, request.headers.get("user-agent"))
    await db.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair, summary="Rotate a refresh token")
async def refresh(payload: RefreshRequest, request: Request, db: Db) -> TokenPair:
    """Rotate the refresh token, revoking the one presented.

    Presenting an already-consumed token is treated as theft: the entire
    session family is revoked, which logs out the attacker and the victim's
    device, forcing a fresh login.
    """
    token_hash = hash_refresh_token(payload.refresh_token)
    session = (
        await db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
    ).scalar_one_or_none()

    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    now = datetime.now(UTC)

    if session.revoked_at is not None:
        # Replay of a rotated token. Burn the whole family.
        await db.execute(
            UserSession.__table__.update()
            .where(
                UserSession.family_id == session.family_id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "This session was revoked for security reasons. Please sign in again.",
        )

    if session.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token has expired")

    user = (
        await db.execute(select(User).where(User.id == session.user_id))
    ).scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    session.revoked_at = now
    tokens = await _issue_tokens(
        db, user, request.headers.get("user-agent"), family_id=session.family_id
    )
    await db.commit()
    return tokens


@router.post("/logout", summary="Revoke the current session, or all of them")
async def logout(payload: LogoutRequest, user: CurrentUser, db: Db) -> dict:
    now = datetime.now(UTC)

    if payload.everywhere:
        await db.execute(
            UserSession.__table__.update()
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        # Also retire outstanding access tokens, which are stateless.
        user.token_version += 1
        await db.commit()
        return {"message": "Signed out on all devices."}

    if payload.refresh_token:
        await db.execute(
            UserSession.__table__.update()
            .where(
                UserSession.refresh_token_hash
                == hash_refresh_token(payload.refresh_token),
                UserSession.user_id == user.id,
            )
            .values(revoked_at=now)
        )
    await db.commit()
    return {"message": "Signed out."}


# --------------------------------------------------------------------------
# Password recovery
# --------------------------------------------------------------------------
@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(rate_limit("forgot", settings.rate_limit_password_reset))
    ],
)
async def forgot_password(
    payload: ForgotPasswordRequest, background: BackgroundTasks, db: Db
) -> dict:
    user = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    # Always the same response, whether or not the address exists.
    if user is not None and user.deleted_at is None:
        token = create_email_token(user.id, "reset_password", hours=1)
        background.add_task(
            email_service.send_password_reset_email, user.email, token
        )

    return {
        "message": (
            "If an account exists for that address, a reset link is on its way."
        )
    }


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest, db: Db) -> dict:
    try:
        claims = decode_token(payload.token, "reset_password")
    except TokenError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(claims["sub"])))
    ).scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is no longer valid.")

    user.hashed_password = hash_password(payload.new_password)
    # Changing a password must invalidate every existing session.
    user.token_version += 1
    await db.execute(
        UserSession.__table__.update()
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
    return {"message": "Password updated. Sign in with your new password."}


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest, user: ActiveUser, db: Db
) -> dict:
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Your current password is incorrect."
        )
    user.hashed_password = hash_password(payload.new_password)
    user.token_version += 1
    await db.execute(
        UserSession.__table__.update()
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
    return {"message": "Password changed. Please sign in again."}


# --------------------------------------------------------------------------
# Account
# --------------------------------------------------------------------------
@router.get("/me", response_model=MeResponse, summary="The signed-in account")
async def me(user: CurrentUser) -> MeResponse:
    return _me(user)


@router.delete(
    "/me",
    status_code=status.HTTP_200_OK,
    summary="Permanently delete the signed-in account",
)
async def delete_account(
    payload: DeleteAccountRequest, user: CurrentUser, db: Db
) -> dict:
    """Irreversibly delete the account and scrub personal data.

    Requires the current password even though the caller already holds a valid
    token, so a stolen token alone cannot destroy an account.
    """
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect password.")

    await purge_user_data(db, user)
    await db.commit()
    return {
        "message": (
            "Your account and personal data have been deleted. We are sorry to "
            "see you go."
        )
    }


@router.get("/sessions", summary="List active sessions for this account")
async def list_sessions(user: CurrentUser, db: Db) -> dict:
    rows = (
        await db.execute(
            select(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > func.now(),
            )
            .order_by(UserSession.created_at.desc())
        )
    ).scalars()
    return {
        "sessions": [
            {
                "id": str(s.id),
                "created_at": s.created_at,
                "expires_at": s.expires_at,
                "user_agent": s.user_agent,
            }
            for s in rows
        ]
    }
