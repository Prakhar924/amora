"""Transactional email (verification and password reset).

Locally this talks to the MailHog container in docker-compose, where every
message is visible at http://localhost:8025. If SMTP is unreachable and
`email_console_fallback` is on, the message is logged instead so development
never blocks on mail delivery.

`smtplib` is synchronous, so sends are pushed to a worker thread.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import anyio

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_sync(to: str, subject: str, text_body: str, html_body: str | None) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
        logger.info("Sent %r to %s", subject, to)
    except (OSError, smtplib.SMTPException) as exc:
        if not settings.email_console_fallback:
            raise
        logger.warning(
            "SMTP unavailable (%s). Falling back to console.\n"
            "--- EMAIL ---\nTo: %s\nSubject: %s\n\n%s\n-------------",
            exc,
            to,
            subject,
            text_body,
        )


async def send_email(
    to: str, subject: str, text_body: str, html_body: str | None = None
) -> None:
    await anyio.to_thread.run_sync(_send_sync, to, subject, text_body, html_body)


def _wrap(title: str, intro: str, action_url: str, button: str) -> str:
    return f"""\
<!doctype html>
<html><body style="margin:0;padding:24px;background:#faf7f8;font-family:
  -apple-system,Segoe UI,Roboto,sans-serif;color:#1c1419">
  <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:16px;
    padding:32px;box-shadow:0 2px 12px rgba(0,0,0,.06)">
    <h1 style="margin:0 0 12px;font-size:22px">{title}</h1>
    <p style="margin:0 0 24px;line-height:1.55;color:#5b4d55">{intro}</p>
    <a href="{action_url}" style="display:inline-block;padding:13px 26px;
      border-radius:999px;background:#e0366b;color:#fff;text-decoration:none;
      font-weight:600">{button}</a>
    <p style="margin:24px 0 0;font-size:12px;color:#9b8c94;line-height:1.5">
      If the button does not work, paste this link into your browser:<br>
      <span style="word-break:break-all">{action_url}</span>
    </p>
  </div>
</body></html>"""


async def send_verification_email(to: str, token: str) -> None:
    url = f"{settings.frontend_base_url}/verify-email?token={token}"
    await send_email(
        to,
        f"Confirm your {settings.app_name} email",
        f"Welcome to {settings.app_name}!\n\n"
        f"Confirm your email address to start matching:\n{url}\n\n"
        "This link expires in 24 hours.",
        _wrap(
            f"Welcome to {settings.app_name}",
            "Confirm your email address to finish setting up your account and "
            "start meeting people.",
            url,
            "Confirm email",
        ),
    )


async def send_password_reset_email(to: str, token: str) -> None:
    url = f"{settings.frontend_base_url}/reset-password?token={token}"
    await send_email(
        to,
        f"Reset your {settings.app_name} password",
        "We received a request to reset your password.\n\n"
        f"Choose a new one here:\n{url}\n\n"
        "This link expires in 1 hour. If you did not request it, ignore this "
        "email and your password will stay the same.",
        _wrap(
            "Reset your password",
            "We received a request to reset your password. This link expires in "
            "one hour. If this was not you, you can safely ignore this email.",
            url,
            "Choose a new password",
        ),
    )
