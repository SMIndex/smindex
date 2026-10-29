"""Featured traders whitelist gate (spec: Whitelist gate).

  * is_allowed(wallet): admin wallets (ADMIN_ADDRESSES) or a featured_whitelist row
  * create_request(): validate, rate limit (3 per IP per hour, 1 per wallet per
    day), one open request per wallet (a repeat updates email and handle and
    says already_requested), save, ONE Telegram message to
    settings.ADMIN_TELEGRAM_CHAT_ID with inline Approve / Reject buttons.
    Nothing is sent to any user channel, digest or group; with no admin chat
    configured nothing is sent at all and the request waits on the admin page.
  * decide(): approve or reject, from the admin route or the Telegram button
    (same code). Approve adds the wallet to featured_whitelist.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from datetime import datetime

from sqlalchemy import text

from app.config import settings
from app.db.database import get_session_factory
from app.services import telegram_queue
from app.utils.logger import get_logger

logger = get_logger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
IP_LIMIT, IP_WINDOW = 3, 3600
_ip_hits: dict[str, list[float]] = defaultdict(list)
_allowed_cache: dict[str, tuple[float, bool]] = {}
CACHE_SEC = 30.0


class RequestError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(f"featured:{ip}".encode()).hexdigest()


def is_admin(wallet: str | None) -> bool:
    return bool(wallet) and wallet.lower() in settings.admin_address_set


async def is_allowed(wallet: str | None) -> bool:
    if not wallet:
        return False
    w = wallet.lower()
    if is_admin(w):
        return True
    hit = _allowed_cache.get(w)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_SEC:
        return hit[1]
    sf = get_session_factory()
    async with sf() as s:
        ok = (await s.execute(text("SELECT 1 FROM featured_whitelist WHERE wallet=:w"), {"w": w})).scalar() is not None
    _allowed_cache[w] = (now, ok)
    return ok


def _ip_ok(ip: str) -> bool:
    now = time.time()
    b = _ip_hits[ip]
    b[:] = [t for t in b if now - t < IP_WINDOW]
    if len(b) >= IP_LIMIT:
        return False
    b.append(now)
    return True


async def _facts(wallet: str) -> str:
    """Short wallet facts for the admin message: Perpl account, HL account
    value (from the stored position read when present), SMINDEX user since."""
    parts = []
    sf = get_session_factory()
    perpl_acct = None
    since = None
    hl_value = None
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT perpl_account_id, created_at FROM users WHERE LOWER(wallet_address)=:w"), {"w": wallet})).first()
        if r:
            perpl_acct, since = r[0], r[1]
        v = (await s.execute(text("SELECT account_value FROM featured_live WHERE wallet=:w"), {"w": wallet})).scalar()
        if v is not None:
            hl_value = float(v)
        if hl_value is None:
            st = (await s.execute(text("SELECT state FROM hl_open_positions WHERE wallet=:w"), {"w": wallet})).scalar()
            if st:
                try:
                    import json
                    hl_value = float((json.loads(st).get("marginSummary") or {}).get("accountValue") or 0)
                except Exception:
                    hl_value = None
    if perpl_acct is None:
        try:
            import asyncio
            from app.services.chain_reader import get_perpl_account_id
            perpl_acct = await asyncio.to_thread(get_perpl_account_id, wallet)
        except Exception:
            perpl_acct = None
    parts.append("Perpl account: " + ("yes" if perpl_acct not in (None, 0, "0", "") else "no"))
    parts.append("HL value: " + (f"${hl_value:,.2f}" if hl_value is not None else "none"))
    parts.append("SMINDEX user since " + (since.strftime("%b %Y") if since else "never signed in"))
    return " · ".join(parts)


async def create_request(*, email: str, x_handle: str, wallet: str, hp: str | None, ip: str) -> dict:
    if hp:
        # honeypot filled: drop silently, the caller still gets "received"
        logger.info("featured whitelist: honeypot hit from %s", _ip_hash(ip)[:10])
        return {"status": "received"}
    email = (email or "").strip()
    handle = (x_handle or "").strip().lstrip("@")
    wallet = (wallet or "").strip()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise RequestError(400, "Enter a valid email address.")
    if not HANDLE_RE.match(handle):
        raise RequestError(400, "X handle: 1 to 15 letters, digits or underscores.")
    if not WALLET_RE.match(wallet):
        raise RequestError(400, "Enter a valid wallet address (0x and 40 hex characters).")
    wallet = wallet.lower()
    if not _ip_ok(ip):
        raise RequestError(429, "Too many requests from this network. Try again in an hour.")
    sf = get_session_factory()
    async with sf() as s:
        if await is_allowed(wallet):
            return {"status": "already_whitelisted"}
        pending = (await s.execute(text(
            "SELECT id FROM featured_whitelist_requests WHERE wallet=:w AND status='pending'"), {"w": wallet})).scalar()
        if pending:
            await s.execute(text(
                "UPDATE featured_whitelist_requests SET email=:e, x_handle=:h, updated_at=UTC_TIMESTAMP() WHERE id=:i"),
                {"e": email, "h": handle, "i": pending})
            await s.commit()
            return {"status": "already_requested"}
        recent = (await s.execute(text(
            "SELECT COUNT(*) FROM featured_whitelist_requests WHERE wallet=:w AND created_at >= UTC_TIMESTAMP() - INTERVAL 1 DAY"),
            {"w": wallet})).scalar()
        if recent:
            raise RequestError(429, "One request per wallet per day.")
        r = await s.execute(text(
            "INSERT INTO featured_whitelist_requests (email, x_handle, wallet, status, created_at, ip_hash) "
            "VALUES (:e, :h, :w, 'pending', UTC_TIMESTAMP(), :ip)"), {"e": email, "h": handle, "w": wallet, "ip": _ip_hash(ip)})
        rid = r.lastrowid
        await s.commit()
    await _notify_admin(rid, email, handle, wallet)
    return {"status": "received", "id": rid}


async def _notify_admin(rid: int, email: str, handle: str, wallet: str) -> None:
    chat = (settings.ADMIN_TELEGRAM_CHAT_ID or "").strip()
    if not chat:
        logger.warning("featured whitelist: request #%d saved, ADMIN_TELEGRAM_CHAT_ID not set, no message sent", rid)
        return
    try:
        facts = await _facts(wallet)
    except Exception as exc:
        facts = f"facts unavailable ({str(exc)[:40]})"
    msg = (f"<b>Featured traders: access request #{rid}</b>\n"
           f"Wallet <code>{wallet}</code>\n"
           f"Email {email} · X @{handle}\n"
           f"{facts}")
    buttons = [[("Approve", f"cb:fwl:approve:{rid}"), ("Reject", f"cb:fwl:reject:{rid}")]]

    async def on_result(sent, error):
        mid = getattr(sent, "message_id", None) if sent is not None else None
        if mid is None and error is None:
            return
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text("UPDATE featured_whitelist_requests SET tg_message_id=:m WHERE id=:i"), {"m": mid, "i": rid})
            await s.commit()
        if error:
            logger.warning("featured whitelist: admin message for #%d failed: %s", rid, error)

    ok = telegram_queue.enqueue(chat, msg, buttons=buttons, dedupe_key=f"featured_wl:{rid}", on_result=on_result)
    logger.info("featured whitelist: request #%d for %s queued to admin chat: %s", rid, wallet[:10], ok)


async def decide(rid: int, approve: bool, by: str, note: str | None = None) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        r = (await s.execute(text(
            "SELECT wallet, status FROM featured_whitelist_requests WHERE id=:i"), {"i": rid})).first()
        if not r:
            raise RequestError(404, "Request not found")
        wallet, status = r[0], r[1]
        new = "approved" if approve else "rejected"
        if status != "pending" and status != new:
            # already decided the other way: allow the admin to flip it
            pass
        await s.execute(text(
            "UPDATE featured_whitelist_requests SET status=:st, note=:n, decided_at=UTC_TIMESTAMP(), decided_by=:b WHERE id=:i"),
            {"st": new, "n": (note or None), "b": by[:42], "i": rid})
        if approve:
            await s.execute(text(
                "INSERT INTO featured_whitelist (wallet, approved_at, approved_by, source) VALUES (:w, UTC_TIMESTAMP(), :b, 'request') "
                "ON DUPLICATE KEY UPDATE approved_at=VALUES(approved_at), approved_by=VALUES(approved_by)"), {"w": wallet, "b": by[:42]})
        else:
            await s.execute(text("DELETE FROM featured_whitelist WHERE wallet=:w"), {"w": wallet})
        await s.commit()
    _allowed_cache.pop(wallet, None)
    logger.info("featured whitelist: #%d %s by %s", rid, new, by[:12])
    return {"id": rid, "wallet": wallet, "status": new}


async def add_wallet(wallet: str, by: str, source: str = "admin") -> dict:
    wallet = wallet.lower()
    if not WALLET_RE.match(wallet):
        raise RequestError(400, "Invalid wallet")
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO featured_whitelist (wallet, approved_at, approved_by, source) VALUES (:w, UTC_TIMESTAMP(), :b, :src) "
            "ON DUPLICATE KEY UPDATE approved_at=VALUES(approved_at), approved_by=VALUES(approved_by), source=VALUES(source)"),
            {"w": wallet, "b": by[:42], "src": source})
        await s.commit()
    _allowed_cache.pop(wallet, None)
    return {"wallet": wallet, "status": "approved"}


async def remove_wallet(wallet: str) -> dict:
    wallet = wallet.lower()
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text("DELETE FROM featured_whitelist WHERE wallet=:w"), {"w": wallet})
        await s.commit()
    _allowed_cache.pop(wallet, None)
    return {"wallet": wallet, "status": "removed"}


async def list_requests(status: str | None, q: str | None, limit: int = 200) -> list[dict]:
    sf = get_session_factory()
    where = []
    params: dict = {"n": limit}
    if status in ("pending", "approved", "rejected"):
        where.append("r.status=:st")
        params["st"] = status
    if q:
        where.append("(r.wallet LIKE :q OR r.email LIKE :q OR r.x_handle LIKE :q)")
        params["q"] = f"%{q.strip().lower()}%"
    sql = ("SELECT r.id, r.email, r.x_handle, r.wallet, r.status, r.note, r.created_at, r.decided_at, r.decided_by, "
           "w.approved_at FROM featured_whitelist_requests r LEFT JOIN featured_whitelist w ON w.wallet=r.wallet "
           + ("WHERE " + " AND ".join(where) if where else "") + " ORDER BY r.created_at DESC LIMIT :n")
    async with sf() as s:
        rows = (await s.execute(text(sql), params)).all()
    return [{"id": r[0], "email": r[1], "x_handle": r[2], "wallet": r[3], "status": r[4], "note": r[5],
             "created_at": r[6].isoformat() if r[6] else None, "decided_at": r[7].isoformat() if r[7] else None,
             "decided_by": r[8], "whitelisted": r[9] is not None} for r in rows]


async def telegram_callback(data: str, chat_id: str, actor: str) -> str:
    """Inline button from the admin message: 'fwl:approve:ID' / 'fwl:reject:ID'.
    Only the configured admin chat may decide."""
    if str(chat_id) != str(settings.ADMIN_TELEGRAM_CHAT_ID or "").strip():
        return "Not allowed from this chat."
    try:
        _, action, rid = data.split(":")
        rid = int(rid)
    except ValueError:
        return "Bad button."
    if action not in ("approve", "reject"):
        return "Bad button."
    try:
        res = await decide(rid, action == "approve", by=f"tg:{actor}"[:42])
    except RequestError as exc:
        return exc.detail
    return f"Request #{rid}: {res['status']} ({res['wallet'][:10]}...)"
