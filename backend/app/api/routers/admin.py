"""Admin moderation surface.

Every route requires `AdminUser`, which returns 404 (not 403) to a
non-admin so the existence of this surface is not advertised.

The photo queue and the report queue are both designed so an automated
classifier can be added later without changing the API: a future job can write
`Report.auto_signal` or pre-set `ProfilePhoto.moderation_status`, and these
endpoints keep working as the human review layer on top.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import AdminUser, Db
from app.models import (
    Match,
    Message,
    ModerationStatus,
    Notification,
    NotificationType,
    Profile,
    ProfilePhoto,
    Report,
    ReportStatus,
    User,
    UserSession,
    UserStatus,
)
from app.schemas.social import (
    AdminPhotoOut,
    AdminReportOut,
    AdminStats,
    ModeratePhotoRequest,
    ResolveReportRequest,
    SuspendUserRequest,
)
from app.services import storage

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats", response_model=AdminStats, summary="Moderation dashboard counters")
async def stats(admin: AdminUser, db: Db) -> AdminStats:
    async def count(stmt) -> int:
        return int((await db.execute(stmt)).scalar_one())

    return AdminStats(
        total_users=await count(
            select(func.count(User.id)).where(User.deleted_at.is_(None))
        ),
        active_users=await count(
            select(func.count(User.id)).where(User.status == UserStatus.ACTIVE)
        ),
        pending_photo_moderation=await count(
            select(func.count(ProfilePhoto.id)).where(
                ProfilePhoto.moderation_status == ModerationStatus.PENDING,
                ProfilePhoto.upload_confirmed.is_(True),
            )
        ),
        open_reports=await count(
            select(func.count(Report.id)).where(
                Report.status.in_([ReportStatus.OPEN, ReportStatus.REVIEWING])
            )
        ),
        matches_created=await count(select(func.count(Match.id))),
        messages_sent=await count(select(func.count(Message.id))),
    )


# --------------------------------------------------------------------------
# Photo moderation
# --------------------------------------------------------------------------
@router.get(
    "/photos",
    response_model=list[AdminPhotoOut],
    summary="Photo moderation queue",
)
async def photo_queue(
    admin: AdminUser,
    db: Db,
    status_filter: ModerationStatus = Query(
        default=ModerationStatus.PENDING, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AdminPhotoOut]:
    rows = (
        await db.execute(
            select(ProfilePhoto, Profile)
            .join(Profile, Profile.id == ProfilePhoto.profile_id)
            .where(
                ProfilePhoto.moderation_status == status_filter,
                # Unconfirmed rows are reservations, not real photos.
                ProfilePhoto.upload_confirmed.is_(True),
            )
            .order_by(ProfilePhoto.created_at.asc())
            .limit(limit)
        )
    ).all()

    return [
        AdminPhotoOut(
            id=photo.id,
            user_id=profile.user_id,
            display_name=profile.display_name,
            url=storage.presign_download(photo.storage_key),
            moderation_status=photo.moderation_status,
            created_at=photo.created_at,
        )
        for photo, profile in rows
    ]


@router.post("/photos/{photo_id}/moderate", summary="Approve or reject a photo")
async def moderate_photo(
    photo_id: uuid.UUID, payload: ModeratePhotoRequest, admin: AdminUser, db: Db
) -> dict:
    """Record a moderation decision and notify the owner.

    Approving a user's first photo is what completes their profile, so
    completeness is recomputed here.
    """
    photo = (
        await db.execute(
            select(ProfilePhoto)
            .where(ProfilePhoto.id == photo_id)
            .options(selectinload(ProfilePhoto.profile))
        )
    ).scalar_one_or_none()
    if photo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found")

    photo.moderation_status = payload.status
    photo.moderation_note = payload.note
    photo.moderated_at = datetime.now(UTC)
    photo.moderated_by_id = admin.id

    profile = photo.profile
    # Flush first: with `autoflush=False` the status assigned above is still
    # only in memory, so the COUNT would otherwise read the pre-decision
    # state and never mark the profile complete.
    await db.flush()

    approved = (
        await db.execute(
            select(func.count(ProfilePhoto.id)).where(
                ProfilePhoto.profile_id == profile.id,
                ProfilePhoto.upload_confirmed.is_(True),
                ProfilePhoto.moderation_status == ModerationStatus.APPROVED,
            )
        )
    ).scalar_one()

    if approved > 0 and profile.completed_at is None and profile.display_name:
        profile.completed_at = datetime.now(UTC)
    elif approved == 0:
        # Losing the last approved photo pulls the profile out of discovery.
        profile.completed_at = None

    is_approved = payload.status == ModerationStatus.APPROVED
    db.add(
        Notification(
            user_id=profile.user_id,
            type=(
                NotificationType.PHOTO_APPROVED
                if is_approved
                else NotificationType.PHOTO_REJECTED
            ),
            title="Photo approved" if is_approved else "Photo not approved",
            body=(
                "Your photo is now live on your profile."
                if is_approved
                else payload.note
                or "That photo did not meet our guidelines. Try another one."
            ),
            payload={"photo_id": str(photo.id)},
        )
    )

    await db.commit()
    return {"message": f"Photo {payload.status.value}."}


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------
@router.get(
    "/reports",
    response_model=list[AdminReportOut],
    summary="Report queue",
)
async def report_queue(
    admin: AdminUser,
    db: Db,
    status_filter: ReportStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AdminReportOut]:
    conditions = []
    if status_filter is not None:
        conditions.append(Report.status == status_filter)
    else:
        conditions.append(
            Report.status.in_([ReportStatus.OPEN, ReportStatus.REVIEWING])
        )

    rows = (
        await db.execute(
            select(Report, User, Profile, Message)
            .join(User, User.id == Report.reported_id)
            .outerjoin(Profile, Profile.user_id == Report.reported_id)
            .outerjoin(Message, Message.id == Report.message_id)
            .where(*conditions)
            # Oldest first: a report queue is worked as a FIFO.
            .order_by(Report.created_at.asc())
            .limit(limit)
        )
    ).all()

    out: list[AdminReportOut] = []
    for report, reported_user, profile, message in rows:
        # Prior-report count is the signal a moderator needs to spot a pattern
        # rather than judging each report in isolation.
        prior = (
            await db.execute(
                select(func.count(Report.id)).where(
                    Report.reported_id == report.reported_id
                )
            )
        ).scalar_one()
        out.append(
            AdminReportOut(
                id=report.id,
                reason=report.reason,
                status=report.status,
                details=report.details,
                created_at=report.created_at,
                reporter_id=report.reporter_id,
                reported_id=report.reported_id,
                reported_display_name=profile.display_name if profile else None,
                reported_email=reported_user.email,
                report_count_against_reported=int(prior),
                conversation_id=report.conversation_id,
                message_id=report.message_id,
                message_body=message.body if message else None,
                photo_id=report.photo_id,
            )
        )
    return out


@router.post("/reports/{report_id}/resolve", summary="Resolve a report")
async def resolve_report(
    report_id: uuid.UUID, payload: ResolveReportRequest, admin: AdminUser, db: Db
) -> dict:
    """Close a report, optionally suspending the reported account.

    The suspension and the resolution are written in one transaction so a
    report cannot be marked actioned without the action landing.
    """
    report = (
        await db.execute(select(Report).where(Report.id == report_id))
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")

    report.status = payload.status
    report.resolution_note = payload.resolution_note
    report.reviewed_by_id = admin.id
    report.reviewed_at = datetime.now(UTC)

    if payload.suspend_days:
        reported = (
            await db.execute(select(User).where(User.id == report.reported_id))
        ).scalar_one_or_none()
        if reported is not None:
            await _apply_suspension(
                db,
                reported,
                payload.suspend_days,
                payload.resolution_note or f"Report upheld: {report.reason.value}",
            )

    await db.commit()
    return {"message": f"Report marked {payload.status.value}."}


# --------------------------------------------------------------------------
# Account enforcement
# --------------------------------------------------------------------------
async def _apply_suspension(db: Db, target: User, days: int, reason: str) -> None:
    target.status = UserStatus.SUSPENDED
    target.suspended_until = datetime.now(UTC) + timedelta(days=days)
    target.suspension_reason = reason
    # Revoke sessions and retire access tokens so the suspension is immediate
    # rather than taking effect when the current token expires.
    target.token_version += 1
    await db.execute(
        UserSession.__table__.update()
        .where(UserSession.user_id == target.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.add(
        Notification(
            user_id=target.id,
            type=NotificationType.ACCOUNT_WARNING,
            title="Your account has been suspended",
            body=reason,
            payload={"until": target.suspended_until.isoformat()},
        )
    )


@router.post("/users/{user_id}/suspend", summary="Suspend an account")
async def suspend_user(
    user_id: uuid.UUID, payload: SuspendUserRequest, admin: AdminUser, db: Db
) -> dict:
    target = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if target is None or target.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if target.is_admin:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Administrators cannot be suspended."
        )

    await _apply_suspension(db, target, payload.days, payload.reason)
    await db.commit()
    return {
        "message": f"Account suspended until {target.suspended_until:%Y-%m-%d}.",
    }


@router.post("/users/{user_id}/reinstate", summary="Lift a suspension")
async def reinstate_user(user_id: uuid.UUID, admin: AdminUser, db: Db) -> dict:
    target = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if target is None or target.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    target.status = UserStatus.ACTIVE
    target.suspended_until = None
    target.suspension_reason = None
    await db.commit()
    return {"message": "Account reinstated."}


@router.get("/users", summary="Search accounts")
async def search_users(
    admin: AdminUser,
    db: Db,
    q: str | None = Query(default=None, description="Email or display-name fragment"),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict:
    stmt = (
        select(User, Profile)
        .outerjoin(Profile, Profile.user_id == User.id)
        .where(User.deleted_at.is_(None))
        .order_by(User.created_at.desc())
        .limit(limit)
    )
    if q:
        pattern = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(User.email).like(pattern)
            | func.lower(Profile.display_name).like(pattern)
        )

    rows = (await db.execute(stmt)).all()
    return {
        "users": [
            {
                "id": str(user.id),
                "email": user.email,
                "display_name": profile.display_name if profile else None,
                "status": user.status.value,
                "is_admin": user.is_admin,
                "suspended_until": user.suspended_until,
                "created_at": user.created_at,
            }
            for user, profile in rows
        ]
    }
