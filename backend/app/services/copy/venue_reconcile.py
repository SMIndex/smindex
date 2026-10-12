"""Venue-truth reconciliation sweep (audit A3/A4, MODAL_REMEDIATION Part D).

Every 10 minutes, for wallets that are BOTH in LIVE_COPY_ALLOWLIST and active
in the last 24h (a live copy attempt or an open copied position), read the
wallet's REAL on-chain Perpl state (the existing chain_reader path, the venue
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
     so this is a review flag, not an accusation, stated in the event).

Mismatch -> `venue_reconciliation_mismatch` risk_event + WARNING log + admin
telegram line. Chain reads are counted and logged per run (allowlist size 1
today -> ~1 read / 10 min).
"""
import asyncio
import time
from datetime import datetime, timedelta

from sqlalchemy import bindparam, text

from app.config import settings
from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

INTERVAL_SEC = 600
FIRST_RUN_DELAY_SEC = 120
SIZE_EPS = 1e-9

_task: asyncio.Task | None = None
_last_run: dict = {}

# Rows seen with NO venue position, keyed by copy_live_positions.id -> how many
# CONSECUTIVE sweeps have seen that. A single empty read is not proof: a failed
# getPosition sub-call inside get_trader_detail is swallowed per market and
# looks exactly like "no position", so one glitchy RPC must never close a money
# record. Two sweeps = 10 minutes of agreement.
_missing_streak: dict[int, int] = {}
DETACH_AFTER_SWEEPS = 2
DETACH_MIN_AGE_SEC = 120          # a just-opened position may not be readable yet


async def _detach_rows(wallet: str, row_ids: list[int], market_id: int, side: str) -> int:
    """Mark copy rows the venue no longer holds as detached (not closed: we did
    not see the close, so there is no close price or realized P/L to record)."""
    sf = get_session_factory()
    async with sf() as s:
        r = await s.execute(text(
            "UPDATE copy_live_positions SET status='detached', current_size=0, "
            "closed_at=UTC_TIMESTAMP(), close_reason='closed_outside_smindex', "
            "updated_at=UTC_TIMESTAMP() WHERE id IN :ids AND status IN ('open','partially_closed')"
        ).bindparams(bindparam("ids", expanding=True)), {"ids": row_ids})
        await s.commit()
    n = r.rowcount or 0
    if n:
        logger.warning("reconcile DETACHED %s row(s) %s (%s market %s) - the venue has held no "
                       "position for %s consecutive sweeps", n, row_ids, side, market_id,
                       DETACH_AFTER_SWEEPS)
        for rid in row_ids:
            _missing_streak.pop(rid, None)
    return n


async def _active_allowlisted_wallets() -> list[str]:
    # Every wallet with live copy activity: the allowlist was removed on
    # 2026-10-12, so the sweep can no longer be scoped by it.
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
    return sorted({r.lower() for r in rows})


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
    detached = 0
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
                "SELECT id, follower_market_id, side, current_size, "
                "TIMESTAMPDIFF(SECOND, opened_at, UTC_TIMESTAMP()) AS age_sec "
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
        ages: dict[tuple, list] = {}
        for pid, mkt, side, cur, age_sec in ours:
            k = (mkt, side)
            claimed[k] = claimed.get(k, 0.0) + float(cur or 0)
            ids.setdefault(k, []).append(pid)
            ages.setdefault(k, []).append(int(age_sec or 0))
        # Only trust an "empty" venue read when the ACCOUNT read itself worked.
        account_read_ok = bool(detail and detail.get("account_id"))
        for (mkt, side), size in claimed.items():
            vp = venue.get(mkt)
            if vp is None and account_read_ok:
                # Close the records the venue does not back - after agreement
                # across sweeps. This is the drift the owner had to fix by hand
                # twice (rows 8/9 on 10-08, row 10 the same day): the sweep used
                # to only warn, forever.
                ripe = [pid for pid, age in zip(ids[(mkt, side)], ages[(mkt, side)])
                        if age >= DETACH_MIN_AGE_SEC]
                for pid in ripe:
                    _missing_streak[pid] = _missing_streak.get(pid, 0) + 1
                due = [pid for pid in ripe if _missing_streak.get(pid, 0) >= DETACH_AFTER_SWEEPS]
                if due:
                    detached += await _detach_rows(w, due, mkt, side)
            else:
                for pid in ids[(mkt, side)]:
                    _missing_streak.pop(pid, None)
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
                               f"market {mkt}: review (manual trading is indistinguishable "
                               f"from an untracked placement)",
                            {"copy_order_id": oid, "market_id": mkt, "venue": vp})

    stats = {"ts": datetime.utcnow().isoformat(), "wallets": len(wallets),
             "chain_reads": reads, "mismatches": mismatches, "detached": detached,
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
            logger.exception("venue reconcile sweep failed: next run in %ss", INTERVAL_SEC)
        await asyncio.sleep(INTERVAL_SEC)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())
        logger.info("venue reconcile sweep started (every %ss, all live-copy wallets)", INTERVAL_SEC)


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
