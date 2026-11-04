"""Unusual Timing compute pipeline (worker process, no Hyperliquid reads):

  daily 03:30 UTC   coin stats -> events (full) -> entries (candidates,
                    exclusions, merge) -> forward moves -> close times ->
                    scores -> clusters -> drift
  hourly at :20     events (big moves and funding flips of the last 48 h) ->
                    forward moves of recent entries -> drift

Each step writes a ut_job_runs row (weight 0) so the CLI and the report can
show when it ran and what it did.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.ut import bars, clusters, coin_stats, drift, entries, events, funders, scores
from app.utils.logger import get_logger

logger = get_logger(__name__)

DAILY_AT = (3, 30)
HOURLY_MIN = 20


async def _step(name: str, coro) -> dict:
    sf = get_session_factory()
    started = datetime.utcnow()
    async with sf() as s:
        r = await s.execute(text("INSERT INTO ut_job_runs (job, started_at) VALUES (:j, :t)"), {"j": name, "t": started})
        await s.commit()
        rid = r.lastrowid
    ok, out = True, {}
    try:
        out = await coro
    except Exception as exc:  # noqa: BLE001
        ok = False
        out = {"error": f"{type(exc).__name__}: {exc}"}
        logger.exception("ut worker step %s failed", name)
    async with sf() as s:
        await s.execute(text("UPDATE ut_job_runs SET finished_at=UTC_TIMESTAMP(), ok=:ok, notes=:n WHERE id=:i"),
                        {"ok": 1 if ok else 0, "n": str(out)[:255], "i": rid})
        await s.commit()
    logger.info("ut worker %s: %s (%.0f s)", name, out, (datetime.utcnow() - started).total_seconds())
    return out


async def run_daily() -> dict:
    out = {}
    out["bars"] = await _step("ut_bars_from_fills", bars.build())
    out["coin_stats"] = await _step("ut_coin_stats", coin_stats.build())
    out["events"] = await _step("ut_events_build", events.build(full=True))
    out["entries"] = await _step("ut_entries_build", entries.build_all())
    out["forward"] = await _step("ut_entries_forward", entries.compute_forward())
    out["close_times"] = await _step("ut_entries_close", entries.close_times())
    out["funders"] = await _step("ut_funders", funders.build())
    out["scores"] = await _step("ut_scores", scores.build())
    out["clusters"] = await _step("ut_clusters", clusters.build())
    out["drift"] = await _step("ut_drift", drift.build())
    return out


async def run_hourly() -> dict:
    out = {}
    out["bars"] = await _step("ut_bars_from_fills_h", bars.build(days=2))
    out["events"] = await _step("ut_events_hourly", events.build(full=False))
    out["forward"] = await _step("ut_entries_forward_h", entries.compute_forward())
    out["drift"] = await _step("ut_drift", drift.build())
    return out


def _next(now: datetime) -> tuple[datetime, str]:
    d = now.replace(hour=DAILY_AT[0], minute=DAILY_AT[1], second=0, microsecond=0)
    if d <= now:
        d += timedelta(days=1)
    h = now.replace(minute=HOURLY_MIN, second=0, microsecond=0)
    if h <= now:
        h += timedelta(hours=1)
    return (d, "daily") if d <= h else (h, "hourly")


async def loop() -> None:
    while True:
        now = datetime.utcnow()
        at, kind = _next(now)
        await asyncio.sleep(max(1.0, (at - now).total_seconds()))
        try:
            if kind == "daily":
                await run_daily()
            else:
                await run_hourly()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ut worker loop: %s", exc)
