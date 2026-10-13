"""Transactional email (verification and password reset).

Three transports, selected by `EMAIL_TRANSPORT`:

* ``resend``  — Resend's HTTPS API. **Required on Render**, whose free and
  paid plans block outbound SMTP ports 25, 465 and 587 entirely, so an SMTP
  transport cannot work there no matter how it is configured.
* ``smtp``    — any SMTP provider. Used by the VPS deployment and by the local
  Mailpit container.
* ``console`` — log the message instead of sending. Development only.

`auto` (the default) picks `resend` when an API key is present, otherwise
`smtp`, otherwise `console`.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import anyio
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


def _resolve_transport() -> str:
    configured = settings.email_transport.lower()
    if configured != "auto":
        return configured
    if settings.resend_api_key:
        return "resend"
    if settings.smtp_host:
        return "smtp"
    return "console"


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------
async def _send_via_resend(
    to: str, subject: str, text_body: str, html_body: str | None
) -> None:
    """Send through Resend's HTTPS API.

    Raises on failure unless the console fallback is enabled: a verification
    email that silently fails leaves the user staring at an empty inbox with
    no way forward.
    """
    payload: dict[str, object] = {
        "from": settings.smtp_from,
        "to": [to],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.status_code >= 400:
        detail = response.text[:500]
        # The single most common Resend failure, and the error message alone
        # does not make the cause obvious.
        if response.status_code == 403 and "resend.dev" in settings.smtp_from:
            raise RuntimeError(
                "Resend rejected the send: the shared resend.dev sender can "
                "only deliver to the address that owns the Resend account. To "
                "email real users you must verify a domain you control and set "
                f"SMTP_FROM to an address on it. Response: {detail}"
            )
        raise RuntimeError(f"Resend API error {response.status_code}: {detail}")

    logger.info("Sent %r to %s via Resend", subject, to)


def _send_via_smtp_sync(
    to: str, subject: str, text_body: str, html_body: str | None
) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
    logger.info("Sent %r to %s via SMTP", subject, to)


def _log_to_console(to: str, subject: str, text_body: str) -> None:
    logger.warning(
        "EMAIL NOT SENT — logging instead.\n"
        "--- EMAIL ---\nTo: %s\nSubject: %s\n\n%s\n-------------",
        to,
        subject,
        text_body,
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
async def send_email(
    to: str, subject: str, text_body: str, html_body: str | None = None
) -> None:
    """Deliver one message, honouring the configured transport.

    Never raises when `email_console_fallback` is on, so a development machine
    without mail configured still works. In production that flag is forbidden,
    so a failure propagates and is logged as an error.
    """
    transport = _resolve_transport()

    try:
        if transport == "resend":
            await _send_via_resend(to, subject, text_body, html_body)
            return
        if transport == "smtp":
            # smtplib is synchronous; keep it off the event loop.
            await anyio.to_thread.run_sync(
                _send_via_smtp_sync, to, subject, text_body, html_body
            )
            return
        _log_to_console(to, subject, text_body)
    except Exception as exc:
        if not settings.email_console_fallback:
            logger.error("Email delivery to %s failed: %s", to, exc)
            raise
        logger.warning(
            "Email transport %r failed (%s); falling back to the console.",
            transport,
            exc,
        )
        _log_to_console(to, subject, text_body)


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
