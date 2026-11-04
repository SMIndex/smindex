"""Bars from what is already stored (owner, 2026-11-04): every candidate fill
in hl_fills carries time, price and size, back to 2026-07-31, and the hourly
sampler hl_fill_events adds listed wallets' fills since 2026-10-27. The
trades feed itself keeps no public trade tape. From those prints:

  * 1 h bars for the 90-day window, kept only for hours with at least
    MIN_PRINTS_HOUR fills (a thin hour understates the range);
  * 1 m bars for the last M1_DAYS days, one per minute that has a print;
    entries.compute_forward uses them only when the 2 h window after the
    entry has at least 70 % of its minutes.

Rows carry src = 'fills'; a Hyperliquid candleSnapshot read later overwrites
them with src = 'hl'. The volume of a fills bar is the candidates' own
volume, a lower bound of the market's, so the moved-market share it gives is
an upper bound: more moved-market flags, fewer hits (the conservative side).
Open and close are the first and last print of the bar.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.ut import common
from app.utils.logger import get_logger

logger = get_logger(__name__)

MIN_PRINTS_HOUR = 20
M1_DAYS = 7


async def _ensure_src_column() -> None:
    sf = get_session_factory()
    async with sf() as s:
        cols = (await s.execute(text("SHOW COLUMNS FROM ut_candles LIKE 'src'"))).all()
        if not cols:
            await s.execute(text("ALTER TABLE ut_candles ADD COLUMN src ENUM('hl','fills') NOT NULL DEFAULT 'hl'"))
            await s.commit()


async def _build(tf: str, since: datetime, min_prints: int) -> int:
    """One pass over hl_fills (and the sampler rows hl_fills does not hold)
    grouped by coin and bar; only perp coins of the universe."""
    step = 60 if tf == "1m" else 3600
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text("SET SESSION group_concat_max_len = 64"))
        rows = (await s.execute(text(
            "SELECT p.coin, FLOOR(UNIX_TIMESTAMP(p.time) / :step) * :step AS b, COUNT(*) n, SUM(p.sz) v, MIN(p.px) l, MAX(p.px) h, "
            "SUBSTRING_INDEX(GROUP_CONCAT(p.px ORDER BY p.time ASC), ',', 1) o, "
            "SUBSTRING_INDEX(GROUP_CONCAT(p.px ORDER BY p.time DESC), ',', 1) c "
            "FROM ("
            "  SELECT f.coin, f.time, f.px, f.sz FROM hl_fills f WHERE f.time >= :a"
            "  UNION ALL"
            "  SELECT CONVERT(e.coin USING utf8mb4) COLLATE utf8mb4_0900_ai_ci, FROM_UNIXTIME(e.ts_ms / 1000), e.px, e.sz FROM hl_fill_events e"
            "  LEFT JOIN hl_fills f2 ON f2.wallet = e.wallet_address AND f2.tid = CAST(e.tid AS UNSIGNED)"
            "  WHERE e.ts_ms >= UNIX_TIMESTAMP(:a) * 1000 AND e.px IS NOT NULL AND e.sz IS NOT NULL AND f2.tid IS NULL"
            ") p JOIN ut_universe u ON u.coin = p.coin AND u.kind IN ('perp','hip3') "
            "GROUP BY p.coin, b HAVING n >= :m"), {"step": step, "a": since, "m": min_prints})).all()
    out = [{"coin": c, "tf": tf, "t": datetime.utcfromtimestamp(int(b)), "o": float(o), "h": float(h), "l": float(l), "c": float(cl),
            "v": float(v or 0)} for c, b, n, v, l, h, o, cl in rows if o and cl]
    written = 0
    async with sf() as s:
        for i in range(0, len(out), 1000):
            await s.execute(text(
                "INSERT INTO ut_candles (coin, tf, t, o, h, l, c, v, src) VALUES (:coin, :tf, :t, :o, :h, :l, :c, :v, 'fills') "
                "ON DUPLICATE KEY UPDATE o=IF(src='hl', o, VALUES(o)), h=IF(src='hl', h, VALUES(h)), l=IF(src='hl', l, VALUES(l)), "
                "c=IF(src='hl', c, VALUES(c)), v=IF(src='hl', v, VALUES(v))"), out[i:i + 1000])
            written += len(out[i:i + 1000])
        await s.commit()
    return written


async def build(days: int = common.WINDOW_DAYS) -> dict:
    """days = 90 for the daily pass; the hourly pass rebuilds the last 2 days only."""
    await _ensure_src_column()
    now = common.now_utc()
    n1h = await _build("1h", now - timedelta(days=days), MIN_PRINTS_HOUR)
    n1m = await _build("1m", now - timedelta(days=min(days, M1_DAYS)), 1)
    sf = get_session_factory()
    async with sf() as s:
        cov = (await s.execute(text(
            "SELECT COUNT(DISTINCT coin), COUNT(*) FROM ut_candles WHERE tf='1h' AND src='fills' AND t >= :a"), {"a": now - timedelta(days=common.WINDOW_DAYS)})).first()
        full = (await s.execute(text(
            "SELECT COUNT(*) FROM (SELECT coin, COUNT(*) n FROM ut_candles WHERE tf='1h' AND t >= :a GROUP BY coin HAVING n >= :h) x"),
            {"a": now - timedelta(days=common.WINDOW_DAYS), "h": int(common.WINDOW_DAYS * 24 * 0.95)})).scalar()
    out = {"bars_1h": n1h, "bars_1m": n1m, "coins_1h_from_fills": int(cov[0] or 0), "hours_1h_from_fills": int(cov[1] or 0),
           "coins_95pct_covered": int(full or 0)}
    logger.info("ut bars from fills: %s", out)
    return out
