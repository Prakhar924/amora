"""Discovery, matching, messaging, safety, and notification schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import LikeAction, ModerationStatus, ReportReason, ReportStatus
from app.schemas.profile import PublicProfile


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------
class DiscoveryFeed(BaseModel):
    profiles: list[PublicProfile]
    # Opaque cursor for the next page. None means the feed is exhausted.
    next_cursor: str | None = None
    exhausted: bool = False


class SwipeRequest(BaseModel):
    target_user_id: uuid.UUID
    action: LikeAction


class SwipeResult(BaseModel):
    recorded: bool
    action: LikeAction
    is_match: bool = False
    match: MatchOut | None = None
    # True when this pair had already been swiped and the decision was updated
    # rather than inserted, which the client uses to avoid a duplicate toast.
    was_update: bool = False


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------
class MatchOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    matched_at: datetime
    profile: PublicProfile
    unread_count: int = 0
    last_message_at: datetime | None = None
    last_message_preview: str | None = None


class MatchList(BaseModel):
    matches: list[MatchOut]
    total: int


# --------------------------------------------------------------------------
# Messaging
# --------------------------------------------------------------------------
class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_id: uuid.UUID
    body: str
    created_at: datetime
    # Echoed back so an optimistically-rendered bubble can be reconciled.
    client_nonce: str | None = None


class SendMessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    client_nonce: str | None = Field(default=None, max_length=64)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Message cannot be empty")
        return cleaned


class MessagePage(BaseModel):
    messages: list[MessageOut]
    next_cursor: str | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    match_id: uuid.UUID
    other_user: PublicProfile
    unread_count: int
    last_message_at: datetime | None
    last_message_preview: str | None
    # False once either side unmatches or a block exists; the client then
    # renders the thread read-only.
    can_send: bool = True


class ConversationList(BaseModel):
    conversations: list[ConversationOut]
    total_unread: int


class MarkReadRequest(BaseModel):
    # Everything up to and including this message becomes read. When omitted,
    # the whole conversation is marked read.
    up_to_message_id: uuid.UUID | None = None


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------
class BlockRequest(BaseModel):
    user_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=200)


class ReportRequest(BaseModel):
    user_id: uuid.UUID
    reason: ReportReason
    details: str | None = Field(default=None, max_length=2000)
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    photo_id: uuid.UUID | None = None


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: ReportReason
    status: ReportStatus
    created_at: datetime


class BlockedUserOut(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    blocked_at: datetime


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    title: str
    body: str | None
    payload: dict
    read_at: datetime | None
    created_at: datetime


class NotificationList(BaseModel):
    notifications: list[NotificationOut]
    unread_count: int


# --------------------------------------------------------------------------
# Admin / moderation
# --------------------------------------------------------------------------
class AdminReportOut(BaseModel):
    id: uuid.UUID
    reason: ReportReason
    status: ReportStatus
    details: str | None
    created_at: datetime
    reporter_id: uuid.UUID
    reported_id: uuid.UUID
    reported_display_name: str | None
    reported_email: str | None
    report_count_against_reported: int
    conversation_id: uuid.UUID | None
    message_id: uuid.UUID | None
    message_body: str | None
    photo_id: uuid.UUID | None


class ResolveReportRequest(BaseModel):
    status: ReportStatus
    resolution_note: str | None = Field(default=None, max_length=1000)
    # Optional enforcement applied to the reported account in the same
    # transaction as the resolution.
    suspend_days: int | None = Field(default=None, ge=1, le=3650)


class AdminPhotoOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    url: str
    moderation_status: ModerationStatus
    created_at: datetime


class ModeratePhotoRequest(BaseModel):
    status: ModerationStatus
    note: str | None = Field(default=None, max_length=500)

    @field_validator("status")
    @classmethod
    def _decision_only(cls, value: ModerationStatus) -> ModerationStatus:
        if value == ModerationStatus.PENDING:
            raise ValueError("Moderation decision must be approved or rejected")
        return value


class SuspendUserRequest(BaseModel):
    days: int = Field(ge=1, le=3650)
    reason: str = Field(min_length=3, max_length=500)


class AdminStats(BaseModel):
    total_users: int
    active_users: int
    pending_photo_moderation: int
    open_reports: int
    matches_created: int
    messages_sent: int


# Resolve the forward reference from SwipeResult -> MatchOut.
SwipeResult.model_rebuild()
