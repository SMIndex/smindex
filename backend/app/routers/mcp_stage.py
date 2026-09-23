"""Stage envelope verification endpoint.

The MCP `stage_order` tool returns an HMAC-signed envelope embedded in a deep
link. The frontend (Trade.tsx) POSTs that envelope here BEFORE pre-filling
the order form. We verify:

  1. signature (HMAC-SHA256 with JWT_SECRET-derived key)
  2. expiry (default 5 minutes)
  3. uid binding — the envelope must have been issued for the currently
     authenticated terminal user

This blocks the phishing path where an attacker calls stage_order (or replays
a leaked link) and tricks a victim into clicking Place on a foreign-crafted
order: the envelope's `uid` won't match the victim's user_id, so verification
fails and the order form stays empty.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.models import User
from app.mcp.stage_sig import verify_payload
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/mcp-stage", tags=["mcp"])


class VerifyRequest(BaseModel):
    envelope: str


class VerifyResponse(BaseModel):
    payload: dict


@router.post("/verify", response_model=VerifyResponse)
async def verify_stage(
    req: VerifyRequest,
    user: User = Depends(get_authenticated_user),
) -> VerifyResponse:
    try:
        payload = verify_payload(req.envelope, expected_user_id=user.id)
    except ValueError as exc:
        logger.warning(
            "MCP stage verify failed user_id=%s reason=%s", user.id, exc
        )
        raise HTTPException(status_code=400, detail=str(exc))
    return VerifyResponse(payload=payload)
