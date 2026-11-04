"""Shared helpers for the Unusual Timing worker: candle loading, disk guard,
data directory, time helpers. No Hyperliquid reads here."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.featured import roundtrips as rt

DATA_DIR = os.environ.get("UT_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "ut_data")
MIN_FREE_GB = float(os.environ.get("UT_MIN_FREE_GB", "1.5"))
WINDOW_DAYS = 90


def free_gb(path: str = "/") -> float:
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return 0.0


def disk_ok() -> bool:
    return free_gb() >= MIN_FREE_GB


def now_utc() -> datetime:
    return datetime.utcnow()


def ms(dt: datetime) -> int:
    return rt.dt_to_ms(dt)


def dt(ms_: int) -> datetime:
    return rt.ms_to_dt(ms_)


async def load_candles(coin: str, tf: str, t0: datetime, t1: datetime) -> list[dict]:
    """Bars of one coin and timeframe in [t0, t1) as {t, T, o, h, l, c, v} with
    t and T in epoch ms (T = end of bar)."""
    step = 60_000 if tf == "1m" else 3_600_000
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT t, o, h, l, c, v FROM ut_candles WHERE coin=:c AND tf=:tf AND t >= :a AND t < :b ORDER BY t"),
            {"c": coin, "tf": tf, "a": t0, "b": t1})).all()
    out = []
    for t, o, h, l, c, v in rows:
        tm = rt.dt_to_ms(t)
        out.append({"t": tm, "T": tm + step - 1, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v or 0)})
    return out


async def candle_coverage(coin: str, tf: str) -> tuple[datetime | None, datetime | None]:
    sf = get_session_factory()
    async with sf() as s:
        a, b = (await s.execute(text("SELECT MIN(t), MAX(t) FROM ut_candles WHERE coin=:c AND tf=:tf"), {"c": coin, "tf": tf})).first()
    return a, b


async def perp_coins() -> set[str]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text("SELECT coin FROM ut_universe WHERE kind IN ('perp','hip3')"))).scalars().all()
    return set(rows)


async def latest_thresholds() -> dict[str, dict]:
    """coin -> latest ut_coin_stats row (threshold, base rate, regime)."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT s.coin, s.big_move_pct, s.base_rate_60m, s.regime, s.median_2h_range FROM ut_coin_stats s JOIN ("
            " SELECT coin, MAX(as_of) mx FROM ut_coin_stats GROUP BY coin) l ON l.coin=s.coin AND l.mx=s.as_of"))).all()
    return {c: {"thr": float(t), "base_rate": float(b), "regime": r, "median_2h_range": (float(m) if m is not None else None)}
            for c, t, b, r, m in rows}


def window_start(days: int = WINDOW_DAYS) -> datetime:
    return now_utc() - timedelta(days=days)
