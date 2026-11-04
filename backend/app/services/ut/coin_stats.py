"""ut_coin_stats: per coin, per day, from the stored 1 h candles: the median
2 h range, the big-move threshold (rules.threshold_pct), the base rate of a
random entry being followed by a big move within 2 h, and the regime tag."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.ut import common, rules
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def build(as_of: date | None = None) -> dict:
    as_of = as_of or common.now_utc().date()
    coins = sorted(await common.perp_coins())
    sf = get_session_factory()
    async with sf() as s:
        have = set((await s.execute(text("SELECT DISTINCT coin FROM ut_candles WHERE tf='1h'"))).scalars().all())
    coins = [c for c in coins if c in have]
    t1 = common.now_utc()
    t0 = t1 - timedelta(days=common.WINDOW_DAYS)
    rows = []
    for coin in coins:
        c1h = await common.load_candles(coin, "1h", t0, t1)
        if len(c1h) < 48:
            continue
        med = rules.median_2h_range_pct(c1h)
        thr = rules.threshold_pct(coin, med)
        rate, bars, up, dn = rules.base_rate(c1h, thr)
        rows.append({"d": as_of, "c": coin, "thr": thr, "br": round(rate, 6), "med": (round(med, 3) if med is not None else None),
                     "rg": rules.regime(c1h), "h": bars, "up": up, "dn": dn})
    async with sf() as s:
        for i in range(0, len(rows), 500):
            await s.execute(text(
                "INSERT INTO ut_coin_stats (as_of, coin, big_move_pct, base_rate_60m, median_2h_range, regime, hours, moves_up, moves_down) "
                "VALUES (:d, :c, :thr, :br, :med, :rg, :h, :up, :dn) ON DUPLICATE KEY UPDATE big_move_pct=VALUES(big_move_pct), "
                "base_rate_60m=VALUES(base_rate_60m), median_2h_range=VALUES(median_2h_range), regime=VALUES(regime), hours=VALUES(hours), "
                "moves_up=VALUES(moves_up), moves_down=VALUES(moves_down)"), rows[i:i + 500])
        await s.commit()
    logger.info("ut coin stats: %d coins as of %s", len(rows), as_of)
    return {"coins": len(rows), "as_of": str(as_of)}
