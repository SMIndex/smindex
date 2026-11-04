"""ut_market_drift derived columns, per coin per hour (spec section 8): the
funding trend over 48 h, open-interest change over 6 h and 24 h, the
abnormal 12 h return against the coin's own 90-day distribution (a z-score
from 1 h candles), the count of flagged wallets that entered in the last
6 h, and a 0 to 100 drift score:

  25 x |z| / 3  +  25 x |oi 24 h| / 30 %  +  15 x |oi 6 h| / 15 %
  + 15 x |funding trend| / 0.05 %/h  +  20 x min(flagged wallets in 6 h / 3, 1)

each term capped at its weight. The raw oi / funding / mark columns are
written by the API reader every hour.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import mean, pstdev

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.ut import common
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _cap(x: float, full: float, weight: float) -> float:
    return min(weight, abs(x) / full * weight) if full > 0 else 0.0


async def build(at: datetime | None = None) -> dict:
    hour = (at or common.now_utc()).replace(minute=0, second=0, microsecond=0)
    sf = get_session_factory()
    async with sf() as s:
        raw = (await s.execute(text(
            "SELECT coin, t, oi, funding, mark FROM ut_market_drift WHERE t >= :a AND t <= :b ORDER BY coin, t"),
            {"a": hour - timedelta(hours=49), "b": hour})).all()
        flagged = (await s.execute(text(
            "SELECT e.coin, COUNT(DISTINCT e.wallet) FROM ut_entries e JOIN ut_wallet_scores w ON w.wallet=e.wallet "
            "AND w.as_of=(SELECT MAX(as_of) FROM ut_wallet_scores) AND w.level <> 'none' WHERE e.time >= :a GROUP BY e.coin"),
            {"a": hour - timedelta(hours=6)})).all()
    fl = {c: int(n) for c, n in flagged}
    by_coin: dict[str, list] = {}
    for c, t, oi, f, m in raw:
        by_coin.setdefault(c, []).append((t, float(oi) if oi is not None else None, float(f) if f is not None else None, float(m) if m is not None else None))
    upd = []
    for coin, rows in by_coin.items():
        cur = next((r for r in rows if r[0] == hour), None)
        if cur is None:
            continue

        def at_(h):
            tt = hour - timedelta(hours=h)
            return next((r for r in rows if r[0] == tt), None)

        r6, r24, r48 = at_(6), at_(24), at_(48)
        oi6 = (cur[1] / r6[1] - 1.0) * 100.0 if r6 and r6[1] and cur[1] else None
        oi24 = (cur[1] / r24[1] - 1.0) * 100.0 if r24 and r24[1] and cur[1] else None
        ftr = (cur[2] - r48[2]) if r48 and r48[2] is not None and cur[2] is not None else None
        c1h = await common.load_candles(coin, "1h", hour - timedelta(days=90), hour + timedelta(hours=1))
        z = None
        if len(c1h) >= 13 * 24:
            closes = [b["c"] for b in c1h]
            rets = [(closes[i] / closes[i - 12] - 1.0) * 100.0 for i in range(12, len(closes)) if closes[i - 12] > 0]
            if len(rets) > 30:
                sd = pstdev(rets[:-1])
                z = (rets[-1] - mean(rets[:-1])) / sd if sd > 0 else None
        n_fl = fl.get(coin, 0)
        score = (_cap(z or 0.0, 3.0, 25) + _cap(oi24 or 0.0, 30.0, 25) + _cap(oi6 or 0.0, 15.0, 15)
                 + _cap((ftr or 0.0) * 100.0, 0.05, 15) + min(20.0, n_fl / 3.0 * 20.0))
        upd.append({"c": coin, "t": hour, "ftr": ftr, "oi6": (round(oi6, 4) if oi6 is not None else None),
                    "oi24": (round(oi24, 4) if oi24 is not None else None), "z": (round(z, 4) if z is not None else None),
                    "n": n_fl, "sc": round(score, 2)})
    async with sf() as s:
        for i in range(0, len(upd), 500):
            await s.execute(text(
                "UPDATE ut_market_drift SET funding_trend_48h=:ftr, oi_change_6h=:oi6, oi_change_24h=:oi24, abn_ret_12h=:z, ut_wallets_6h=:n, "
                "drift_score=:sc WHERE coin=:c AND t=:t"), upd[i:i + 500])
        await s.execute(text("DELETE FROM ut_market_drift WHERE t < UTC_TIMESTAMP() - INTERVAL 100 DAY"))
        await s.commit()
    logger.info("ut drift: %d coins at %s", len(upd), hour)
    return {"coins": len(upd), "at": str(hour)}
