"""The single door every Telegram alert goes through (spec Part 3).

send_alert() checks, in order: the user's link, the alert toggle, a persistent
dedupe window, quiet hours (only failures + liquidation warnings pass), then
the per-user hourly limit (overflow is held and sent as ONE digest message).
Every decision is written to telegram_alert_log; delivered rows get the real
Telegram message_id from the queue's delivery callback.
"""
import asyncio
import time
from collections import deque
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services import telegram_queue
from app.services.alerts import prefs as prefs_mod
from app.utils.logger import get_logger

logger = get_logger(__name__)

DIGEST_FLUSH_SEC = 600
_sent_times: dict[int, deque] = {}          # user_id -> monotonic send times (last hour)
_digest: dict[int, list[tuple[str, str]]] = {}  # user_id -> [(alert_type, first line)]
_digest_task: asyncio.Task | None = None


async def _link_for(user_id: int | None, wallet: str | None) -> tuple[int, str, str] | None:
    sf = get_session_factory()
    async with sf() as s:
        if user_id is not None:
            row = (await s.execute(text(
                "SELECT u.id, u.wallet_address, t.chat_id FROM users u JOIN telegram_links t "
                "ON t.user_id = u.id WHERE u.id = :u AND t.is_active = 1 AND t.chat_id IS NOT NULL"),
                {"u": user_id})).first()
        else:
            row = (await s.execute(text(
                "SELECT u.id, u.wallet_address, t.chat_id FROM users u JOIN telegram_links t "
                "ON t.user_id = u.id WHERE u.wallet_address = :w AND t.is_active = 1 "
                "AND t.chat_id IS NOT NULL"), {"w": (wallet or "").lower()})).first()
    return (int(row[0]), row[1].lower(), str(row[2])) if row else None


async def _log(user_id: int, alert_type: str, status: str, dedupe_key: str | None,
               message: str, error: str | None = None) -> int:
    sf = get_session_factory()
    async with sf() as s:
        r = await s.execute(text(
            "INSERT INTO telegram_alert_log (user_id, alert_type, status, dedupe_key, preview, "
            "error, created_at) VALUES (:u, :t, :st, :k, :p, :e, UTC_TIMESTAMP())"),
            {"u": user_id, "t": alert_type, "st": status, "k": (dedupe_key or "")[:160] or None,
             "p": message[:240], "e": error})
        await s.commit()
        return int(r.lastrowid)


def _on_result_for(log_id: int):
    async def _cb(sent, error):
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(
                "UPDATE telegram_alert_log SET status = :st, tg_message_id = :m, error = :e, "
                "delivered_at = UTC_TIMESTAMP() WHERE id = :i"),
                {"st": "sent" if error is None else "failed",
                 "m": getattr(sent, "message_id", None), "e": error, "i": log_id})
            await s.commit()
    return _cb


def _in_quiet_hours(g: dict) -> bool:
    if not g.get("quiet_enabled"):
        return False
    from zoneinfo import ZoneInfo
    try:
        hour = datetime.now(ZoneInfo(g.get("tz") or "UTC")).hour
    except Exception:
        hour = datetime.now(timezone.utc).hour
    a, b = int(g.get("quiet_start", 23)), int(g.get("quiet_end", 7))
    return (a <= hour < b) if a < b else (hour >= a or hour < b)


async def _seen_recently(user_id: int, dedupe_key: str, hours: float) -> bool:
    sf = get_session_factory()
    async with sf() as s:
        n = (await s.execute(text(
            "SELECT COUNT(*) FROM telegram_alert_log WHERE user_id = :u AND dedupe_key = :k "
            "AND status IN ('queued','sent','digested') "
            "AND created_at >= UTC_TIMESTAMP() - INTERVAL :h MINUTE"),
            {"u": user_id, "k": dedupe_key[:160], "h": int(hours * 60)})).scalar()
    return bool(n)


async def send_alert(*, alert_type: str, message: str, user_id: int | None = None,
                     wallet: str | None = None, dedupe_key: str | None = None,
                     dedupe_hours: float = 0.0, buttons: list | None = None,
                     force: bool = False) -> dict:
    """Returns {"status": ..., "log_id": ...}. Never raises into the producer."""
    try:
        link = await _link_for(user_id, wallet)
        if link is None:
            return {"status": "not_linked"}
        uid, _w, chat = link
        p = await prefs_mod.get_prefs(uid)
        meta = prefs_mod.ALERT_TYPES.get(alert_type, {})
        if not force and not p.get(alert_type, {}).get("enabled"):
            return {"status": "disabled"}
        if dedupe_key and dedupe_hours > 0 and await _seen_recently(uid, dedupe_key, dedupe_hours):
            return {"status": "duplicate"}
        g = p["_global"]["params"]
        if not force and not meta.get("critical") and _in_quiet_hours(g):
            return {"status": "quiet_hours", "log_id": await _log(uid, alert_type, "quiet", dedupe_key, message)}
        now = time.monotonic()
        dq = _sent_times.setdefault(uid, deque())
        while dq and now - dq[0] > 3600:
            dq.popleft()
        limit = int(g.get("hourly_limit", 20))
        if not force and len(dq) >= limit:
            _digest.setdefault(uid, []).append((alert_type, message.split("\n", 1)[0][:120]))
            return {"status": "digested", "log_id": await _log(uid, alert_type, "digested", dedupe_key, message)}
        log_id = await _log(uid, alert_type, "queued", dedupe_key, message)
        ok = telegram_queue.enqueue(chat, message, buttons=buttons,
                                    dedupe_key=f"{alert_type}:{dedupe_key}:{chat}" if dedupe_key else None,
                                    on_result=_on_result_for(log_id))
        if not ok:
            sf = get_session_factory()
            async with sf() as s:
                await s.execute(text("UPDATE telegram_alert_log SET status='dropped' WHERE id=:i"), {"i": log_id})
                await s.commit()
            return {"status": "dropped", "log_id": log_id}
        dq.append(now)
        return {"status": "queued", "log_id": log_id}
    except Exception as exc:
        logger.exception("send_alert(%s) failed", alert_type)
        return {"status": "error", "error": str(exc)[:200]}


async def _flush_digests() -> None:
    for uid in list(_digest):
        items = _digest.pop(uid, [])
        if not items:
            continue
        lines = [f"• {t.replace('_', ' ')}: {first}" for t, first in items[:20]]
        more = f"\n…and {len(items) - 20} more" if len(items) > 20 else ""
        msg = (f"<b>Alert digest</b> — {len(items)} alert(s) held back by your hourly limit:\n"
               + "\n".join(lines) + more)
        await send_alert(alert_type="digest", message=msg, user_id=uid,
                         dedupe_key=f"digest:{int(time.time())}", force=True)


async def _digest_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=DIGEST_FLUSH_SEC)
        except asyncio.TimeoutError:
            pass
        try:
            await _flush_digests()
        except Exception:
            logger.exception("alert digest flush failed")


def start(stop: asyncio.Event) -> None:
    global _digest_task
    if _digest_task is None or _digest_task.done():
        _digest_task = asyncio.get_event_loop().create_task(_digest_loop(stop))
