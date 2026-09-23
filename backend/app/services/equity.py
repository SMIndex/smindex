"""Real trader equity (cumulative-PnL) series for sparklines / profile curves.

Source: the `leaderboard_snapshots` table (per-wallet pnl_total captured every 30 min by
the leaderboard snapshot job — ws_manager, 1800s). This is REAL historical data — no fabrication.

Behavior (matches the selected leaderboard timeframe):
  * timeframe '24h' / '7d' / '30d' -> filter timestamp >= now - window
  * timeframe 'all'                -> full available history (no lower bound)
Then sort ascending and downsample AFTER filtering to `points` (preserving the first and
last real points). Empty list if a wallet has no snapshot history in the range. Cached briefly.
"""
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.models import LeaderboardSnapshot
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TTL = 60.0
# window in days per timeframe; 'all' -> None (no lower bound, full history)
_TF_DAYS = {"24h": 1, "7d": 7, "30d": 30, "all": None}
_cache: dict[str, dict] = {}   # key "wallet:timeframe:points:period" -> {"data", "ts"}


def _cutoff(timeframe: str, days: int | None):
    """Lower-bound datetime for the range, or None for full history."""
    if days is not None:
        return datetime.utcnow() - timedelta(days=days)
    d = _TF_DAYS.get(timeframe, 7)
    return None if d is None else datetime.utcnow() - timedelta(days=d)


def _dedupe_hourly(rows: list[tuple]) -> list[float]:
    """(timestamp, pnl) rows -> clean value series: strictly time-ascending,
    ONE point per hour bucket (the last of that hour), non-finite dropped.
    Duplicate/same-hour snapshot points made the row sparklines jagged
    (PROFILE_QUALITY_REPORT C2)."""
    by_hour: dict[int, float] = {}
    for ts, pnl in rows:
        if ts is None or pnl is None:
            continue
        try:
            v = float(pnl)
        except (TypeError, ValueError):
            continue
        bucket = int(ts.timestamp() // 3600) if hasattr(ts, "timestamp") else int(ts // 3600)
        by_hour[bucket] = v   # rows arrive time-ascending: last of the hour wins
    return [round(by_hour[b], 2) for b in sorted(by_hour)]


def _downsample(series: list[float], n: int) -> list[float]:
    """Evenly downsample to n points while ALWAYS keeping the first and last real points."""
    L = len(series)
    if L <= n:
        return series
    if n <= 2:
        return [series[0], series[-1]]
    step = (L - 1) / (n - 1)
    return [series[round(i * step)] for i in range(n)]


async def get_series(
    wallet: str, timeframe: str = "7d", points: int = 160, period: str = "all", days: int | None = None,
    exchange: str = "perpl",
) -> list[float]:
    """Cumulative-PnL points for one wallet across the timeframe (downsampled). Real data."""
    wallet = (wallet or "").lower()
    key = f"{exchange}:{wallet}:{days if days is not None else timeframe}:{points}:{period}"
    now = time.monotonic()
    c = _cache.get(key)
    if c and now - c["ts"] < _TTL:
        return c["data"]
    cutoff = _cutoff(timeframe, days)
    sf = get_session_factory()
    async with sf() as s:
        q = select(LeaderboardSnapshot.timestamp, LeaderboardSnapshot.pnl_total).where(
            LeaderboardSnapshot.wallet_address == wallet,
            LeaderboardSnapshot.period == period,
            LeaderboardSnapshot.exchange == exchange,
        )
        if cutoff is not None:
            q = q.where(LeaderboardSnapshot.timestamp >= cutoff)
        rows = (await s.execute(q.order_by(LeaderboardSnapshot.timestamp))).all()
    data = _downsample(_dedupe_hourly(rows), points)
    _cache[key] = {"data": data, "ts": now}
    return data


async def get_series_batch(
    wallets: list[str], timeframe: str = "7d", points: int = 60, period: str = "all", days: int | None = None,
    exchange: str = "perpl",
) -> dict[str, list[float]]:
    """Series for many wallets in ONE query (downsampled per wallet). Real data."""
    uniq = list(dict.fromkeys((w or "").lower() for w in wallets if w))
    if not uniq:
        return {}
    tfkey = f"{exchange}:{days if days is not None else timeframe}"
    out: dict[str, list[float]] = {}
    missing = []
    now = time.monotonic()
    for w in uniq:
        c = _cache.get(f"{w}:{tfkey}:{points}:{period}")
        if c and now - c["ts"] < _TTL:
            out[w] = c["data"]
        else:
            missing.append(w)
    if missing:
        cutoff = _cutoff(timeframe, days)
        sf = get_session_factory()
        async with sf() as s:
            q = select(LeaderboardSnapshot.wallet_address, LeaderboardSnapshot.timestamp,
                       LeaderboardSnapshot.pnl_total).where(
                LeaderboardSnapshot.wallet_address.in_(missing),
                LeaderboardSnapshot.period == period,
                LeaderboardSnapshot.exchange == exchange,
            )
            if cutoff is not None:
                q = q.where(LeaderboardSnapshot.timestamp >= cutoff)
            rows = (await s.execute(
                q.order_by(LeaderboardSnapshot.wallet_address, LeaderboardSnapshot.timestamp)
            )).all()
        grouped: dict[str, list[tuple]] = {w: [] for w in missing}
        for waddr, ts, pnl in rows:
            grouped.setdefault(waddr, []).append((ts, pnl))
        for w in missing:
            data = _downsample(_dedupe_hourly(grouped.get(w, [])), points)
            _cache[f"{w}:{tfkey}:{points}:{period}"] = {"data": data, "ts": now}
            out[w] = data
    return out
