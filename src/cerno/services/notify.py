from __future__ import annotations

import html
import logging

import httpx

from cerno.config import Settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


class NotifyError(Exception):
    """Raised when an access-request email could not be delivered."""


def _row(label: str, value: str) -> str:
    return (
        '<tr>'
        f'<td style="padding:10px 16px;border-bottom:1px solid #18181b;'
        f'font:11px/1.4 ui-monospace,monospace;letter-spacing:.12em;'
        f'text-transform:uppercase;color:#8c86c9;white-space:nowrap;'
        f'vertical-align:top">{html.escape(label)}</td>'
        f'<td style="padding:10px 16px;border-bottom:1px solid #18181b;'
        f'font:14px/1.5 -apple-system,Segoe UI,sans-serif;color:#e9e8f2">'
        f'{html.escape(value)}</td>'
        '</tr>'
    )


def _render_html(
    *, name: str, email: str, organization: str, role: str, message: str, ref: str
) -> str:
    message_block = (
        f'<div style="margin-top:20px">'
        f'<div style="font:11px/1.4 ui-monospace,monospace;letter-spacing:.12em;'
        f'text-transform:uppercase;color:#6b6b78;margin-bottom:8px">Message</div>'
        f'<div style="font:14px/1.6 -apple-system,Segoe UI,sans-serif;color:#c9c8d6;'
        f'white-space:pre-wrap;border-left:2px solid #3d3a66;padding-left:14px">'
        f'{html.escape(message)}</div></div>'
        if message and message != "-"
        else ""
    )
    return f"""\
<!doctype html>
<html><body style="margin:0;background:#000;padding:32px 0">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0"
        style="width:560px;max-width:92%;background:#060509;border:1px solid #1f1d33">
        <tr><td style="padding:24px 24px 20px;border-bottom:1px solid #18181b">
          <span style="font:600 18px/1 Space Grotesk,Arial,sans-serif;
            letter-spacing:.2em;color:#fff">CERNO</span>
          <span style="font:10px/1 ui-monospace,monospace;letter-spacing:.18em;
            color:#b7b1ff;margin-left:10px">ACCESS REQUEST</span>
        </td></tr>
        <tr><td style="padding:24px 8px 8px">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            {_row("Name", name)}
            {_row("Email", email)}
            {_row("Organization", organization)}
            {_row("Role", role)}
            {_row("Ref", ref)}
          </table>
          <div style="padding:0 16px">{message_block}</div>
        </td></tr>
        <tr><td style="padding:18px 24px 22px;border-top:1px solid #18181b">
          <div style="font:11px/1.5 ui-monospace,monospace;color:#56565f">
            Reply directly to this email to reach {html.escape(email)}.<br>
            CERNO · INTELLIGENCE SYSTEM · A NEMUKAI SYSTEM
          </div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def send_access_request_email(
    settings: Settings,
    *,
    name: str,
    email: str,
    organization: str,
    role: str | None,
    message: str | None,
    ref: str,
) -> str | None:
    """Deliver an access request to the team via Resend.

    Returns the provider message id on success. Raises NotifyError when no
    provider is configured or delivery fails, so the caller can surface a
    retry to the user (there is no DB fallback).
    """
    if not settings.resend_api_key:
        logger.warning("access_request.notify_no_provider ref=%s", ref)
        raise NotifyError("email provider not configured")

    role_text = role or "-"
    message_text = message or "-"
    text = (
        f"New Cerno access request\n\n"
        f"Name:         {name}\n"
        f"Email:        {email}\n"
        f"Organization: {organization}\n"
        f"Role:         {role_text}\n"
        f"Ref:          {ref}\n\n"
        f"Message:\n{message_text}\n"
    )
    payload = {
        "from": settings.resend_from_email,
        "to": [settings.contact_email],
        "reply_to": email,
        "subject": f"Cerno access request: {organization}",
        "text": text,
        "html": _render_html(
            name=name,
            email=email,
            organization=organization,
            role=role_text,
            message=message_text,
            ref=ref,
        ),
    }
    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json=payload,
            timeout=12.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("access_request.notify_error ref=%s error=%s", ref, exc)
        raise NotifyError("email delivery failed") from exc

    if response.status_code >= 400:
        logger.warning(
            "access_request.notify_failed ref=%s status=%s", ref, response.status_code
        )
        raise NotifyError(f"email provider returned {response.status_code}")

    message_id = None
    try:
        message_id = response.json().get("id")
    except ValueError:
        pass
    logger.info("access_request.notify_sent ref=%s message_id=%s", ref, message_id)
    return message_id
