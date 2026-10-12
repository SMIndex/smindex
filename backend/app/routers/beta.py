"""Beta basics (2026-10-07): one-time risk acknowledgement per user (shown before
any real trade, copy or auto-copy is enabled) and a feedback inbox that reaches
the admins' Telegram. Tables are created at startup (migration v30)."""
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import User
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["beta"])

RISK_ACK_VERSION = "2026-10-beta-1"

DDL = [
    "CREATE TABLE IF NOT EXISTS user_risk_ack ("
    " user_id INT NOT NULL PRIMARY KEY, version VARCHAR(32) NOT NULL, acked_at DATETIME NOT NULL)"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    "CREATE TABLE IF NOT EXISTS beta_feedback ("
    " id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NULL, wallet VARCHAR(42) NULL,"
    " message TEXT NOT NULL, page VARCHAR(200) NULL, contact VARCHAR(120) NULL, created_at DATETIME NOT NULL)"
    " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
]


# --- risk acknowledgement -------------------------------------------------

@router.get("/me/risk-ack")
async def get_risk_ack(user: User = Depends(get_authenticated_user)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text("SELECT version, acked_at FROM user_risk_ack WHERE user_id = :u"),
                             {"u": user.id})).first()
    current = bool(r) and r[0] == RISK_ACK_VERSION
    return {"acked": current, "version": RISK_ACK_VERSION,
            "acked_at": r[1].isoformat() if r and r[1] else None}


class RiskAck(BaseModel):
    version: str


@router.post("/me/risk-ack")
async def post_risk_ack(req: RiskAck, user: User = Depends(get_authenticated_user)) -> dict:
    if req.version != RISK_ACK_VERSION:
        raise HTTPException(409, "the risk notice changed; reload and read it again")
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO user_risk_ack (user_id, version, acked_at) VALUES (:u, :v, UTC_TIMESTAMP()) "
            "ON DUPLICATE KEY UPDATE version = VALUES(version), acked_at = VALUES(acked_at)"),
            {"u": user.id, "v": RISK_ACK_VERSION})
        await s.commit()
    return await get_risk_ack(user)


# --- feedback --------------------------------------------------------------

class Feedback(BaseModel):
    message: str = Field(min_length=3, max_length=4000)
    page: Optional[str] = Field(None, max_length=200)
    contact: Optional[str] = Field(None, max_length=120)


_fb_hits: dict[str, deque] = defaultdict(deque)
FEEDBACK_PER_HOUR = 5


async def _optional_user(authorization: Optional[str] = Header(None)) -> Optional[User]:
    if not authorization:
        return None
    try:
        return await get_authenticated_user(authorization)
    except HTTPException:
        return None


@router.post("/feedback")
async def post_feedback(req: Feedback, request: Request, user: Optional[User] = Depends(_optional_user)) -> dict:
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    now = time.monotonic()
    dq = _fb_hits[ip]
    while dq and now - dq[0] > 3600:
        dq.popleft()
    if len(dq) >= FEEDBACK_PER_HOUR:
        raise HTTPException(429, "Thanks, we already have several messages from you this hour. Try again later.")
    dq.append(now)
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO beta_feedback (user_id, wallet, message, page, contact, created_at) "
            "VALUES (:u, :w, :m, :p, :c, UTC_TIMESTAMP())"),
            {"u": user.id if user else None, "w": user.wallet_address.lower() if user else None,
             "m": req.message.strip(), "p": req.page, "c": req.contact})
        await s.commit()
        admins = [a for a in settings.admin_address_set]
        chats = [r[0] for r in (await s.execute(text(
            "SELECT t.chat_id FROM telegram_links t JOIN users u ON u.id = t.user_id "
            "WHERE t.is_active = 1 AND t.chat_id IS NOT NULL AND LOWER(u.wallet_address) IN :a"
        ).bindparams(__import__("sqlalchemy").bindparam("a", expanding=True)), {"a": admins or ["-"]})).all()]
    if chats:
        import html
        from app.services import telegram_queue
        who = (user.wallet_address[:10] + "…") if user else "anonymous"
        msg = (f"<b>SMINDEX beta feedback</b> from {who}\n"
               + (f"Page: {html.escape(req.page)}\n" if req.page else "")
               + (f"Contact: {html.escape(req.contact)}\n" if req.contact else "")
               + "\n" + html.escape(req.message.strip())[:3500])
        for c in chats:
            telegram_queue.enqueue(str(c), msg)
    return {"received": True}
