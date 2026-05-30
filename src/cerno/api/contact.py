from __future__ import annotations

import logging
import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from cerno.api.deps import SettingsDep
from cerno.services.notify import NotifyError, send_access_request_email

router = APIRouter(tags=["contact"])
logger = logging.getLogger(__name__)

_SOFT_OK_ID = "00000000-0000-0000-0000-000000000000"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccessRequestBody(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=254)
    organization: str = Field(min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=160)
    message: str | None = Field(default=None, max_length=4000)
    # Honeypot: real users never see or fill this.
    company: str | None = Field(default=None, max_length=200)


class AccessRequestResponse(BaseModel):
    ok: bool = True
    id: str


@router.post("/contact", response_model=AccessRequestResponse)
def submit_access_request(
    body: AccessRequestBody,
    settings: SettingsDep,
    request: Request,
) -> AccessRequestResponse:
    # Honeypot tripped — pretend success, send nothing.
    if body.company:
        logger.info("access_request.honeypot_tripped email=%s", body.email[:64])
        return AccessRequestResponse(ok=True, id=_SOFT_OK_ID)

    email = body.email.strip()
    if not _EMAIL_RE.match(email):
        logger.info("access_request.invalid_email email=%s", email[:64])
        return AccessRequestResponse(ok=True, id=_SOFT_OK_ID)

    ref = str(uuid.uuid4())
    try:
        message_id = send_access_request_email(
            settings,
            name=body.name.strip(),
            email=email,
            organization=body.organization.strip(),
            role=(body.role or "").strip() or None,
            message=(body.message or "").strip() or None,
            ref=ref,
        )
    except NotifyError as exc:
        logger.warning("access_request.delivery_failed ref=%s reason=%s", ref, exc)
        raise HTTPException(status_code=502, detail="could not deliver request") from exc

    logger.info(
        "access_request.delivered ref=%s org=%s message_id=%s",
        ref,
        body.organization.strip()[:64],
        message_id,
    )
    return AccessRequestResponse(ok=True, id=message_id or ref)


__all__ = ["router"]
