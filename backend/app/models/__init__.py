"""Import every model so Alembic's autogenerate and `Base.metadata` see them."""

from app.models.enums import (
    Gender,
    LikeAction,
    ModerationStatus,
    NotificationType,
    ReportReason,
    ReportStatus,
    UserStatus,
)
from app.models.interest import Interest, UserInterest
from app.models.messaging import Conversation, Message, MessageRead
from app.models.moderation import Block, Report
from app.models.notification import Notification
from app.models.profile import Profile, ProfilePhoto, UserPreference
from app.models.swipe import Like, Match
from app.models.user import Session as UserSession
from app.models.user import User

__all__ = [
    "Block",
    "Conversation",
    "Gender",
    "Interest",
    "Like",
    "LikeAction",
    "Match",
    "Message",
    "MessageRead",
    "ModerationStatus",
    "Notification",
    "NotificationType",
    "Profile",
    "ProfilePhoto",
    "Report",
    "ReportReason",
    "ReportStatus",
    "User",
    "UserInterest",
    "UserPreference",
    "UserSession",
    "UserStatus",
]
