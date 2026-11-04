"""Unusual Timing reader schedule, in the API process: the Hyperliquid reads
(services/ut/hl.py) at 02:30 UTC daily and at five past every hour. The
compute lives in the worker process (scripts/ut_worker.py)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.services.ut import hl
from app.utils.logger import get_logger

logger = get_logger(__name__)

DAILY_AT = (2, 30)
HOURLY_MIN = 5
_task: asyncio.Task | None = None
_last: dict = {}
_running = False


async def run_daily() -> dict:
    global _running
    if _running:
        return {"skipped": "running"}
    _running = True
    run = hl.Run("ut_reader_daily")
    await run.open()
    ok = True
    try:
        out = await hl.daily(run)
        _last["daily"] = {"at": datetime.utcnow().isoformat(), **out, "weight": run.weight}
        return out
    except Exception as exc:  # noqa: BLE001
        ok = False
        run.notes.append(f"error {type(exc).__name__}: {exc}"[:120])
        logger.exception("ut reader daily failed")
        return {"error": str(exc)}
    finally:
        _running = False
        await run.close(ok)


async def run_hourly() -> dict:
    run = hl.Run("ut_reader_hourly")
    await run.open()
    ok = True
    try:
        out = await hl.hourly(run)
        _last["hourly"] = {"at": datetime.utcnow().isoformat(), **out, "weight": run.weight}
        return out
    except Exception as exc:  # noqa: BLE001
        ok = False
        run.notes.append(f"error {type(exc).__name__}: {exc}"[:120])
        logger.exception("ut reader hourly failed")
        return {"error": str(exc)}
    finally:
        await run.close(ok)


def _next_daily(now: datetime) -> datetime:
    t = now.replace(hour=DAILY_AT[0], minute=DAILY_AT[1], second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def _next_hourly(now: datetime) -> datetime:
    t = now.replace(minute=HOURLY_MIN, second=0, microsecond=0)
    return t if t > now else t + timedelta(hours=1)


async def _first_pass_needed() -> bool:
    """No universe snapshot yet (first deploy): read now instead of waiting
    for 02:30 UTC. Also true when yesterday's daily pass stopped at the cap
    and left coins without candles (the daily pass picks them up at 02:30
    anyway, so only the empty case triggers here)."""
    from sqlalchemy import text
    from app.db.database import get_session_factory
    sf = get_session_factory()
    async with sf() as s:
        n = (await s.execute(text("SELECT COUNT(*) FROM ut_universe"))).scalar()
        if int(n or 0) == 0:
            return True
        # a restart in the middle of the first candle pass: resume when a
        # candidate coin has no 1 h bars at all and today's weight is left
        missing = (await s.execute(text(
            "SELECT COUNT(DISTINCT f.coin) FROM hl_fills f JOIN ut_universe u ON u.coin = f.coin AND u.kind IN ('perp','hip3') "
            "LEFT JOIN ut_candles c ON c.coin = f.coin AND c.tf = '1h' AND c.t >= UTC_TIMESTAMP() - INTERVAL 2 DAY "
            "WHERE f.time >= UTC_TIMESTAMP() - INTERVAL 7 DAY AND f.dir LIKE 'Open%' AND c.coin IS NULL"))).scalar()
    return int(missing or 0) > 0 and (await hl.weight_today()) < hl.DAILY_WEIGHT_CAP - 500


async def _loop(stop: asyncio.Event) -> None:
    await asyncio.sleep(90)       # let the API settle after a restart
    try:
        if await _first_pass_needed():
            logger.info("ut reader: first pass now (no universe snapshot yet)")
            await run_daily()
    except Exception as exc:  # noqa: BLE001
        logger.warning("ut reader first pass: %s", exc)
    while not stop.is_set():
        now = datetime.utcnow()
        nd, nh = _next_daily(now), _next_hourly(now)
        nxt = min(nd, nh)
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(1.0, (nxt - now).total_seconds()))
            break
        except asyncio.TimeoutError:
            pass
        try:
            if nxt == nd:
                await run_daily()
            else:
                await run_hourly()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ut reader loop: %s", exc)


def start(stop: asyncio.Event) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(stop))
        logger.info("ut reader started (daily %02d:%02d UTC, hourly :%02d, cap %d weight/day)", DAILY_AT[0], DAILY_AT[1], HOURLY_MIN, hl.DAILY_WEIGHT_CAP)


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _task = None


def status() -> dict:
    return {"running": _running, "last": _last, "cap": hl.DAILY_WEIGHT_CAP, "wpm": hl.UT_WPM}
