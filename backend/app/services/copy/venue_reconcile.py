"""Venue-truth reconciliation sweep (audit A3/A4, MODAL_REMEDIATION Part D).

Every 10 minutes, for wallets that are BOTH in LIVE_COPY_ALLOWLIST and active
in the last 24h (a live copy attempt or an open copied position), read the
wallet's REAL on-chain Perpl state (the existing chain_reader path — the venue
truth a modified client cannot fake) and reconcile:

  1. copy_live_positions open rows: the venue must show a position on that
     market/side with size >= our claimed current_size (venue size is NET of
     manual + copied trading, so venue >= ours is normal; venue < ours or
     none/opposite side means our records claim something the wallet does not
     hold -> HARD mismatch).
  2. attempts expired-without-result (audit A4): if the wallet holds a venue
     position on the expired attempt's market that our open rows do NOT
     account for, the "never placed" assumption may be wrong -> flagged for
     review (manual trading is indistinguishable from an untracked placement,
     so this is a review flag, not an accusation — stated in the event).

Mismatch -> `venue_reconciliation_mismatch` risk_event + WARNING log + admin
telegram line. Chain reads are counted and logged per run (allowlist size 1
today -> ~1 read / 10 min).
"""
import asyncio
import time
from datetime import datetime, timedelta

from sqlalchemy import text

from app.config import settings
from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

INTERVAL_SEC = 600
FIRST_RUN_DELAY_SEC = 120
SIZE_EPS = 1e-9

_task: asyncio.Task | None = None
_last_run: dict = {}


async def _active_allowlisted_wallets() -> list[str]:
    allow = {w.lower() for w in settings.LIVE_COPY_ALLOWLIST}
    if not allow:
        return []
    d24 = datetime.utcnow() - timedelta(hours=24)
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT DISTINCT follower_wallet FROM copy_orders "
            "WHERE mode = 'live_manual' AND created_at >= :d24 "
            "UNION "
            "SELECT DISTINCT follower_wallet FROM copy_live_positions "
            "WHERE status IN ('open','partially_closed')"
        ), {"d24": d24})).scalars().all()
    return [w for w in (r.lower() for r in rows) if w in allow]


async def _venue_positions(wallet: str) -> dict | None:
    """On-chain venue truth via the existing chain_reader path (executor)."""
    from app.services.chain_reader import get_trader_detail
    return await asyncio.get_event_loop().run_in_executor(None, get_trader_detail, wallet)


async def _flag(wallet: str, message: str, detail: dict) -> None:
    from app.services.copy import audit
    logger.warning("VENUE RECONCILIATION MISMATCH %s: %s", wallet[:10], message)
    await audit.write_risk_event(
        wallet, "venue_reconciliation_mismatch",
        detail={"severity": "review", "message": message, **detail})
    # admin telegram (best-effort; admins = ADMIN_ADDRESSES linked chats)
    try:
        from app.services.telegram_bot import notify_wallet
        for admin in settings.admin_address_set:
            await notify_wallet(admin,
                f"⚠️ <b>Venue reconciliation mismatch</b>\n"
                f"wallet <code>{wallet}</code>\n{message}")
    except Exception:
        logger.exception("reconcile admin notify failed")


async def run_once() -> dict:
    t0 = time.monotonic()
    wallets = await _active_allowlisted_wallets()
    reads = 0
    mismatches = 0
    d24 = datetime.utcnow() - timedelta(hours=24)
    sf = get_session_factory()

    for w in wallets:
        try:
            detail = await _venue_positions(w)
        except Exception as exc:
            logger.warning("reconcile venue read failed for %s: %s", w[:10], exc)
            continue
        finally:
            reads += 1
        venue = {}
        for p in (detail or {}).get("positions", []):
            venue[p["market_id"]] = p

        async with sf() as s:
            ours = (await s.execute(text(
                "SELECT id, follower_market_id, side, current_size "
                "FROM copy_live_positions WHERE follower_wallet = :w "
                "AND status IN ('open','partially_closed')"), {"w": w})).all()
            expired = (await s.execute(text(
                "SELECT id, market_id, symbol FROM copy_orders "
                "WHERE follower_wallet = :w AND mode = 'live_manual' "
                "AND status = 'failed' AND error_message LIKE 'expired:%' "
                "AND created_at >= :d24"), {"w": w, "d24": d24})).all()

        # (1) our open claims vs venue net position
        claimed: dict[tuple, float] = {}
        ids: dict[tuple, list] = {}
        for pid, mkt, side, cur in ours:
            k = (mkt, side)
            claimed[k] = claimed.get(k, 0.0) + float(cur or 0)
            ids.setdefault(k, []).append(pid)
        for (mkt, side), size in claimed.items():
            vp = venue.get(mkt)
            if vp is None or vp.get("side") != side:
                mismatches += 1
                await _flag(w, f"copy position(s) {ids[(mkt, side)]} claim {side} "
                               f"{size:g} on market {mkt} but the venue shows "
                               f"{'no position' if vp is None else 'the OPPOSITE side'}",
                            {"market_id": mkt, "claimed_side": side, "claimed_size": size,
                             "venue": vp})
            elif float(vp.get("size") or 0) + SIZE_EPS < size:
                mismatches += 1
                await _flag(w, f"venue {side} size {vp.get('size')} on market {mkt} is "
                               f"SMALLER than copy-claimed {size:g} (rows {ids[(mkt, side)]})",
                            {"market_id": mkt, "claimed_size": size, "venue_size": vp.get("size")})

        # (2) expired attempts vs unexplained venue positions (review flag)
        for oid, mkt, sym in expired:
            vp = venue.get(mkt)
            if vp is not None and (mkt, vp.get("side")) not in claimed:
                mismatches += 1
                await _flag(w, f"attempt #{oid} ({sym}) expired as never-placed but the "
                               f"venue holds an unaccounted {vp.get('side')} position on "
                               f"market {mkt} — review (manual trading is indistinguishable "
                               f"from an untracked placement)",
                            {"copy_order_id": oid, "market_id": mkt, "venue": vp})

    stats = {"ts": datetime.utcnow().isoformat(), "wallets": len(wallets),
             "chain_reads": reads, "mismatches": mismatches,
             "duration_sec": round(time.monotonic() - t0, 1)}
    _last_run.update(stats)
    logger.info("venue reconcile sweep: %s", stats)
    return stats


async def _loop() -> None:
    await asyncio.sleep(FIRST_RUN_DELAY_SEC)
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("venue reconcile sweep failed — next run in %ss", INTERVAL_SEC)
        await asyncio.sleep(INTERVAL_SEC)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())
        logger.info("venue reconcile sweep started (every %ss, allowlist-only)", INTERVAL_SEC)


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None


def get_last_run() -> dict:
    return dict(_last_run)
