"""Enumerations shared by models and schemas.

These are stored as native Postgres enums. `values_callable` is used at the
column sites so the *values* (lowercase) are persisted rather than the Python
member names.
"""

from __future__ import annotations

import enum


class UserStatus(str, enum.Enum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"
    DELETED = "deleted"


class Gender(str, enum.Enum):
    WOMAN = "woman"
    MAN = "man"
    NONBINARY = "nonbinary"
    OTHER = "other"


class LikeAction(str, enum.Enum):
    LIKE = "like"
    PASS = "pass"
    SUPERLIKE = "superlike"


class ModerationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReportReason(str, enum.Enum):
    SPAM = "spam"
    HARASSMENT = "harassment"
    INAPPROPRIATE_CONTENT = "inappropriate_content"
    FAKE_PROFILE = "fake_profile"
    UNDERAGE = "underage"
    SCAM = "scam"
    OTHER = "other"


class ReportStatus(str, enum.Enum):
    OPEN = "open"
    REVIEWING = "reviewing"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"


class NotificationType(str, enum.Enum):
    NEW_MATCH = "new_match"
    NEW_MESSAGE = "new_message"
    NEW_LIKE = "new_like"
    PHOTO_APPROVED = "photo_approved"
    PHOTO_REJECTED = "photo_rejected"
    ACCOUNT_WARNING = "account_warning"
