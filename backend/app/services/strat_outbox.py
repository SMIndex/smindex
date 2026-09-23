"""Strategy Telegram outbox drain (prompt Part 5). Runs in the API process.

The strategy WORKER writes strat_telegram_outbox rows (cross-process). This loop
drains them through the EXISTING telegram_queue (same throttle/dedupe, no second
bot). There is NO per-user notification-preference store, so Phase-1 strategy
alerts go ONLY to admin-linked Telegram chats (admins opted in by linking) — never
fanned out to users. If no admin has linked Telegram, the row is marked handled
and logged (nothing to deliver). Recorded in the report.
"""
from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import func, select

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import User, TelegramLink
from app.db.strategy_models import StratTelegramOutbox, StratAlertPref
from app.services import telegram_queue

logger = logging.getLogger("strat_outbox")

_INTERVAL_S = 20


async def _admin_chat_ids(session) -> list[tuple[str, str]]:
    """(chat_id, wallet) for every admin-linked Telegram chat."""
    admins = settings.admin_address_set
    if not admins:
        return []
    rows = (await session.execute(
        select(TelegramLink.chat_id, User.wallet_address)
        .join(User, User.id == TelegramLink.user_id)
        .where(TelegramLink.is_active.is_(True), TelegramLink.chat_id.isnot(None),
               func.lower(User.wallet_address).in_(admins))
    )).all()
    return [(r[0], (r[1] or "").lower()) for r in rows if r[0]]


def _model_of(row: StratTelegramOutbox) -> str | None:
    """'M1'..'M6' for model rows (doc 17 step 6): alert kinds are '<model>_<event>'."""
    k = row.kind or ""
    if len(k) >= 2 and k[0] == "M" and k[1].isdigit() and (len(k) == 2 or k[2] == "_"):
        return k[:2]
    return None


async def _muted(session, wallets: list[str], model: str) -> set[str]:
    if not wallets:
        return set()
    rows = (await session.execute(
        select(StratAlertPref.wallet).where(StratAlertPref.model == model, StratAlertPref.enabled.is_(False),
                                            StratAlertPref.wallet.in_(wallets)))).all()
    return {r[0] for r in rows}


async def drain_once() -> int:
    sf = get_session_factory()
    sent = 0
    async with sf() as s:
        rows = (await s.execute(
            select(StratTelegramOutbox).where(StratTelegramOutbox.sent.is_(False))
            .order_by(StratTelegramOutbox.ts).limit(50))).scalars().all()
        if not rows:
            return 0
        admin_chats = await _admin_chat_ids(s)
        for row in rows:
            chats = admin_chats
            model = _model_of(row)
            if model and not row.chat_id:
                muted = await _muted(s, [w for _, w in admin_chats], model)
                chats = [(c, w) for c, w in admin_chats if w not in muted]
            targets = [row.chat_id] if row.chat_id else [c for c, _ in chats]
            if not targets:
                # nothing to deliver (no admin linked) — mark handled, don't spin
                row.sent = True
                row.sent_ts = int(time.time() * 1000)
                logger.info("outbox row %d has no recipient (no admin Telegram link); marked handled", row.id)
                continue
            for chat in targets:
                telegram_queue.enqueue(chat, row.message, dedupe_key=f"{row.dedupe_key or row.id}:{chat}")
            row.sent = True
            row.sent_ts = int(time.time() * 1000)
            sent += 1
        await s.commit()
    return sent


async def run(stop_event: asyncio.Event) -> None:
    logger.info("strat outbox drain started (%ds)", _INTERVAL_S)
    while not stop_event.is_set():
        try:
            await drain_once()
        except Exception:  # noqa: BLE001
            logger.exception("strat outbox drain failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_INTERVAL_S)
        except asyncio.TimeoutError:
            pass
