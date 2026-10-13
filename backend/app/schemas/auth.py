"""Authentication request/response schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.config import settings
from app.core.security import password_problems
from app.models.enums import Gender, UserStatus
from app.services.age import age_on, is_eligible_age


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=2, max_length=60)
    date_of_birth: date
    gender: Gender
    # Explicit, affirmative consent. Both must be true; a default of False
    # means a client that omits them is rejected rather than silently opted in.
    accept_terms: bool = False
    accept_privacy: bool = False

    @field_validator("password")
    @classmethod
    def _password_is_strong(cls, value: str) -> str:
        problems = password_problems(value)
        if problems:
            raise ValueError("Password " + ", ".join(problems))
        return value

    @field_validator("date_of_birth")
    @classmethod
    def _must_be_adult(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("Date of birth cannot be in the future")
        if not is_eligible_age(value):
            raise ValueError(
                f"You must be at least {settings.minimum_age} years old to use "
                f"{settings.app_name}"
            )
        if age_on(value) > settings.maximum_age:
            raise ValueError("Please enter a valid date of birth")
        return value

    @field_validator("accept_terms", "accept_privacy")
    @classmethod
    def _must_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("You must accept the Terms of Service and Privacy Policy")
        return value

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None
    # Revoke every session for this user, not just the current device.
    everywhere: bool = False


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access-token lifetime in seconds")


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=10, max_length=200)

    @field_validator("new_password")
    @classmethod
    def _password_is_strong(cls, value: str) -> str:
        problems = password_problems(value)
        if problems:
            raise ValueError("Password " + ", ".join(problems))
        return value


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)

    @field_validator("new_password")
    @classmethod
    def _password_is_strong(cls, value: str) -> str:
        problems = password_problems(value)
        if problems:
            raise ValueError("Password " + ", ".join(problems))
        return value


class VerifyEmailRequest(BaseModel):
    token: str


class DeleteAccountRequest(BaseModel):
    password: str
    # Typed confirmation guards against an accidental client-side call.
    confirmation: str = Field(description='Must be the literal string "DELETE"')

    @field_validator("confirmation")
    @classmethod
    def _must_confirm(cls, value: str) -> str:
        if value.strip().upper() != "DELETE":
            raise ValueError('Confirmation must be the word "DELETE"')
        return value


class MeResponse(BaseModel):
    """The authenticated user's own account view. Never shown to anyone else."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    status: UserStatus
    is_admin: bool
    email_verified: bool
    age: int
    has_profile: bool
    profile_complete: bool
    created_at: datetime
