"""Admin alerts for live entries by flagged wallets (spec section 9).

The trades feed (scripts/hl_trades_feed.py) sees every fill; when a Watch,
Strong or Cluster wallet opens or adds over $100K it inserts a row into
ut_alerts_sent with sent_at NULL (one per wallet per coin per 10 minutes).
This poller, in the API process where the Telegram queue lives, sends every
pending row to ADMIN_TELEGRAM_CHAT_ID and nowhere else, within 2 s of the
insert, and stamps sent_at from the queue's delivery callback. Rows older
than 15 minutes are never sent (they are stamped with an error instead) so
a restart cannot replay stale alerts.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from sqlalchemy import bindparam, text

from app.config import settings
from app.db.database import get_session_factory
from app.services import telegram_queue
from app.utils.logger import get_logger

logger = get_logger(__name__)

POLL_SEC = 2.0
STALE_MIN = 15
_task: asyncio.Task | None = None


def format_alert(row: dict) -> str:
    p = row.get("payload") or {}
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except ValueError:
            p = {}
    w = row["wallet"]
    lvl = str(row.get("level") or "").capitalize()
    hr = p.get("hit_rate")
    hr_s = f"{float(hr) * 100:.0f}% ({p.get('hits')}/{p.get('entries')})" if hr is not None else "n/a"
    age = p.get("age_days")
    age_s = f"{float(age):.0f} days" if age is not None else "unknown"
    lev = p.get("leverage")
    lev_s = f"{float(lev):.0f}x" if lev else "n/a"
    cl = p.get("cluster_id")
    cl_s = f"cluster #{cl} ({p.get('cluster_size')} wallets)" if cl else "no cluster"
    size = float(row.get("notional") or 0)
    kind = "added" if p.get("is_add") else "opened"
    lines = [
        f"<b>Unusual timing: {lvl} wallet {kind} {row['coin']} {row['side']}</b>",
        f"<code>{w}</code>",
        f"Size ${size:,.0f} at {lev_s} · score {p.get('score')} · hit rate {hr_s}",
        f"Events preceded {p.get('events_preceded', 0)} · misses {p.get('misses', 'n/a')} · worst loss ${float(p.get('worst_loss_usd') or 0):,.0f}",
        f"Wallet age {age_s} · {cl_s}",
    ]
    if p.get("test"):
        lines.insert(0, "<b>[test]</b> sample alert from <code>ut alerts test</code>")
    return "\n".join(lines)


async def _send_one(row: dict) -> bool:
    chat = (settings.ADMIN_TELEGRAM_CHAT_ID or "").strip()
    rid = int(row["id"])
    sf = get_session_factory()
    if not chat:
        async with sf() as s:
            await s.execute(text("UPDATE ut_alerts_sent SET error='ADMIN_TELEGRAM_CHAT_ID not set' WHERE id=:i"), {"i": rid})
            await s.commit()
        logger.warning("ut alert #%d: ADMIN_TELEGRAM_CHAT_ID not set, nothing sent", rid)
        return False

    async def on_result(sent, error):
        async with sf() as s2:
            if error:
                await s2.execute(text("UPDATE ut_alerts_sent SET error=:e WHERE id=:i"), {"e": str(error)[:200], "i": rid})
                logger.warning("ut alert #%d: send failed to chat %s: %s", rid, chat, error)
            else:
                mid = getattr(sent, "message_id", None)
                await s2.execute(text("UPDATE ut_alerts_sent SET sent_at=UTC_TIMESTAMP(3), chat_id=:c WHERE id=:i"), {"c": chat, "i": rid})
                logger.info("ut alert #%d delivered to chat %s (message_id %s) wallet %s %s %s", rid, chat, mid, row["wallet"], row["coin"], row["side"])
            await s2.commit()

    ok = telegram_queue.enqueue(chat, format_alert(row), on_result=on_result)
    if not ok:
        async with sf() as s:
            await s.execute(text("UPDATE ut_alerts_sent SET error='queue refused' WHERE id=:i"), {"i": rid})
            await s.commit()
    return ok


async def pending() -> list[dict]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT id, wallet, coin, side, notional, level, payload, created_at FROM ut_alerts_sent "
            "WHERE sent_at IS NULL AND error IS NULL AND chat_id IS NULL ORDER BY id LIMIT 20"))).mappings().all()
        out = []
        for r in rows:
            age_min = (datetime.utcnow() - r["created_at"]).total_seconds() / 60.0
            if age_min > STALE_MIN:
                await s.execute(text("UPDATE ut_alerts_sent SET error='stale, not sent' WHERE id=:i"), {"i": r["id"]})
                continue
            out.append(dict(r))
        await s.commit()
    # mark as taken so a slow callback does not let the next poll send twice
    if out:
        async with sf() as s:
            await s.execute(text("UPDATE ut_alerts_sent SET chat_id='queued' WHERE id IN :i").bindparams(bindparam("i", expanding=True)),
                            {"i": [r["id"] for r in out]})
            await s.commit()
    return out


async def _loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            for row in await pending():
                await _send_one(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ut live poller: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SEC)
        except asyncio.TimeoutError:
            pass


def start(stop: asyncio.Event) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(stop))
        logger.info("ut live alert poller started (every %.0f s, admin chat only)", POLL_SEC)


async def stop_task() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _task = None
