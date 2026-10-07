import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import User, TelegramLink
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])

BOT_USERNAME = ""  # Will be set on first call if bot is running


@router.post("/generate-link-code")
async def generate_link_code(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Generate a unique code for linking Telegram account."""
    code = secrets.token_urlsafe(16)
    expires = datetime.utcnow() + timedelta(minutes=10)

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user.id)
        )
        link = result.scalar_one_or_none()

        if link and link.is_active and link.chat_id:
            raise HTTPException(status_code=409, detail="Telegram already linked. Unlink first.")

        if link:
            link.link_code = code
            link.link_code_expires = expires
            link.is_active = False
            link.chat_id = None
        else:
            link = TelegramLink(
                user_id=user.id,
                link_code=code,
                link_code_expires=expires,
                is_active=False,
            )
            session.add(link)
        await session.commit()

    # Build bot URL
    bot_url = f"https://t.me/PerplCopyBot?start={code}"
    if settings.TELEGRAM_BOT_TOKEN:
        # Try to get actual bot username
        try:
            from app.services.telegram_bot import _app
            if _app:
                bot_info = await _app.bot.get_me()
                bot_url = f"https://t.me/{bot_info.username}?start={code}"
        except Exception:
            pass

    return {
        "code": code,
        "bot_url": bot_url,
        "expires_in_seconds": 600,
    }


@router.get("/status")
async def get_telegram_status(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Check if Telegram is linked for this user."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user.id)
        )
        link = result.scalar_one_or_none()

    if not link or not link.is_active or not link.chat_id:
        return {"linked": False}

    # Mask chat_id for privacy
    chat_id = link.chat_id
    masked = "***" + chat_id[-3:] if len(chat_id) > 3 else "***"

    return {
        "linked": True,
        "chat_id_masked": masked,
        "linked_at": link.linked_at.isoformat() if link.linked_at else None,
    }


@router.delete("/unlink")
async def unlink_telegram(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Unlink Telegram from account."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user.id)
        )
        link = result.scalar_one_or_none()
        if link:
            link.is_active = False
            link.chat_id = None
            await session.commit()

    return {"success": True}


@router.get("/queue-stats")
async def telegram_queue_stats(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Observability for the alert send queue: depth + sent/failed counters.
    Admin only (ADMIN_ADDRESSES)."""
    from app.routers.admin import require_admin
    await require_admin(user)
    from app.services import telegram_queue
    return telegram_queue.get_stats()


# ---------------------------------------------------------------------------
# Alert settings (overnight Part B, spec Part 3). Owner = JWT user, always.
# ---------------------------------------------------------------------------
from typing import Optional as _Opt

from pydantic import BaseModel as _BM
from sqlalchemy import text as _text


class PrefUpdate(_BM):
    enabled: _Opt[bool] = None
    params: _Opt[dict] = None


@router.get("/prefs")
async def get_alert_prefs(user: User = Depends(get_authenticated_user)) -> dict:
    from app.services.alerts import prefs as P
    cur = await P.get_prefs(user.id)
    groups = []
    for gid, glabel in P.GROUPS:
        groups.append({"id": gid, "label": glabel, "alerts": [
            {"type": t, "label": m["label"], "enabled": cur[t]["enabled"],
             "params": cur[t]["params"], "defaults": m["params"], "default_enabled": m["default"],
             "delay": m["delay"], "critical": bool(m.get("critical"))}
            for t, m in P.ALERT_TYPES.items() if m["group"] == gid]})
    return {"groups": groups, "global": cur["_global"]["params"],
            "global_defaults": P.GLOBAL_DEFAULTS, "account_sweep_sec": P.ACCOUNT_SWEEP_SEC}


@router.put("/prefs/{alert_type}")
async def put_alert_pref(alert_type: str, req: PrefUpdate,
                         user: User = Depends(get_authenticated_user)) -> dict:
    from app.services.alerts import prefs as P
    try:
        return await P.set_pref(user.id, alert_type, req.enabled, req.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/test")
async def send_test_alert(user: User = Depends(get_authenticated_user)) -> dict:
    from app.services.alerts import gateway
    r = await gateway.send_alert(
        alert_type="test", user_id=user.id, force=True,
        message=f"<b>SMINDEX test message</b>\nYour alerts reach this chat. Sent {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC.",
        dedupe_key=f"test:{datetime.utcnow():%Y%m%d%H%M%S}")
    if r.get("status") == "not_linked":
        raise HTTPException(status_code=400, detail="Link Telegram first")
    return r


@router.get("/alert-log")
async def my_alert_log(limit: int = 50, user: User = Depends(get_authenticated_user)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(_text(
            "SELECT id, alert_type, status, preview, tg_message_id, error, created_at, delivered_at "
            "FROM telegram_alert_log WHERE user_id = :u ORDER BY id DESC LIMIT :l"),
            {"u": user.id, "l": max(1, min(limit, 200))})).mappings().all()
    return {"rows": [{**dict(r), "created_at": r["created_at"].isoformat(),
                      "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None}
                     for r in rows]}


class TriggerReg(_BM):
    oid: str
    market_id: int
    side: str          # position side the trigger protects: long | short
    kind: str          # sl | tp
    trigger_px: float
    size: _Opt[float] = None


@router.post("/triggers")
async def register_trigger(req: TriggerReg, user: User = Depends(get_authenticated_user)) -> dict:
    """The app reports each TP/SL it placed so the server-side account watch
    can tell WHICH one fired when the position shrinks (no tab needed)."""
    if req.side not in ("long", "short") or req.kind not in ("sl", "tp") or req.trigger_px <= 0:
        raise HTTPException(status_code=400, detail="bad trigger")
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(_text(
            "INSERT INTO alert_triggers (user_id, market_id, side, kind, trigger_px, size, oid, active, created_at) "
            "VALUES (:u, :m, :sd, :k, :px, :sz, :o, 1, UTC_TIMESTAMP()) "
            "ON DUPLICATE KEY UPDATE trigger_px = VALUES(trigger_px), size = VALUES(size), active = 1"),
            {"u": user.id, "m": req.market_id, "sd": req.side, "k": req.kind,
             "px": req.trigger_px, "sz": req.size, "o": req.oid[:40]})
        await s.commit()
    return {"registered": True}


@router.delete("/triggers/{oid}")
async def unregister_trigger(oid: str, user: User = Depends(get_authenticated_user)) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        r = await s.execute(_text("UPDATE alert_triggers SET active = 0 WHERE user_id = :u AND oid = :o"),
                            {"u": user.id, "o": oid[:40]})
        await s.commit()
    return {"deactivated": r.rowcount}


class ClientAlertEvent(_BM):
    type: str
    symbol: str
    market_id: int
    count: int = 1
    reason: str = ""


@router.post("/events")
async def client_alert_event(req: ClientAlertEvent, user: User = Depends(get_authenticated_user)) -> dict:
    """Events only the app itself observes (it performs the action): leftover
    TP/SL it cancelled. Owner-only; one type accepted."""
    if req.type != "tpsl_leftover":
        raise HTTPException(status_code=400, detail="unsupported event type")
    from app.services.alerts import gateway
    import html as _html
    n = max(1, min(req.count, 20))
    msg = (f"<b>Leftover TP/SL cancelled</b> — {_html.escape(req.symbol[:24])}\n"
           f"{n} order{'s' if n > 1 else ''} cancelled: "
           f"{_html.escape(req.reason[:120]) or 'position was closed'}")
    return await gateway.send_alert(alert_type="tpsl_leftover", message=msg, user_id=user.id,
                                    dedupe_key=f"leftover:{req.market_id}:{datetime.utcnow():%Y%m%d%H%M}",
                                    dedupe_hours=0.05)
