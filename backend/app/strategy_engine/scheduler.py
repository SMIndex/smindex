"""Feature scheduler (prompt Part 3.9). Runs inside the worker.

On start: seeds state/params/feed-meta (every strategy paper). Each tick:
computes the funding gauge (per minute), the regime gate and bias (their
cadences), logs a per-source feed-health heartbeat with the last-message age,
then runs the evaluator (all seven strategy rows, paper). When a feed has few
rows yet, the feature is written from what exists and labelled — never skipped,
never fabricated.

Retention (prompt Part 2/3) runs once a day and logs what it pruned.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import text

from app.strategy_engine.features import funding_gauge as fg
from app.strategy_engine.features import regime as regime_mod
from app.strategy_engine.features import bias as bias_mod
from app.strategy_engine.features import timing as timing_mod
from app.strategy_engine import seed as seed_mod
from app.db.strategy_models import StratGauge, StratRegime, StratBias, StratTelegramOutbox
from sqlalchemy.dialects.mysql import insert as mysql_insert

logger = logging.getLogger("strategy_engine.scheduler")

_FEED_TABLES = {
    "hl_candles": "strat_candles",
    "hl_trades_1m": "strat_trades_1m",
    "hl_book_5s": "strat_book_5s",
    "hl_oi_1m": "strat_oi_1m",
    "funding": "strat_funding",
    "liquidations": "strat_liquidations",
}

_MIN_MS = 60_000


class Scheduler:
    def __init__(self, session_factory, assets: list[str]) -> None:
        self._sf = session_factory
        self._assets = assets
        self._last_gauge_min: dict[str, int] = {}
        self._last_bias_5min: dict[str, int] = {}
        self._last_retention_day = -1
        self._last_15m = -1
        self.feeds = None                      # set by the worker once feeds start (oracle liveness)
        from .evaluator import Evaluator
        self._evaluator = Evaluator(session_factory, assets)
        # Models M1–M6 (docs 10–17): separate evaluator, own 15m boundary detection,
        # BTC+ETH from config/models.yaml; a fault here never touches 01–06.
        from .model_runner import ModelEvaluator
        from .mind import config as mind_cfg
        self._models = ModelEvaluator(session_factory, mind_cfg.assets())

    async def start(self) -> None:
        async with self._sf() as s:
            summary = await seed_mod.seed_all(s)
        logger.info("seeded: %d strategies + %d models, %d new parameters (%s)",
                    summary["strategies"], summary.get("models", 0), summary["parameters_seeded"], summary["parameter_counts"])

    async def _feed_health(self, s) -> dict:
        now_ms = int(time.time() * 1000)
        health = {}
        for name, table in _FEED_TABLES.items():
            row = (await s.execute(text(f"SELECT COUNT(*), MAX(ts) FROM {table}"))).first()
            cnt = int(row[0] or 0)
            last_ts = int(row[1]) if row[1] is not None else None
            age_s = (now_ms - last_ts) / 1000.0 if last_ts else None
            health[name] = {"rows": cnt, "last_ts": last_ts, "age_s": age_s}
        return health

    async def _hist_8h_equiv(self, s, coin: str) -> list[float]:
        rows = (await s.execute(text(
            "SELECT predicted_rate FROM strat_funding WHERE venue='hl' AND coin=:c "
            "AND predicted_rate IS NOT NULL ORDER BY ts DESC LIMIT 43200"), {"c": coin})).all()
        return [float(r[0]) * 8.0 for r in rows]

    async def _latest_hl_funding(self, s, coin: str):
        r = (await s.execute(text(
            "SELECT predicted_rate FROM strat_funding WHERE venue='hl' AND coin=:c "
            "ORDER BY ts DESC LIMIT 1"), {"c": coin})).first()
        return float(r[0]) if r and r[0] is not None else None

    async def _latest_binance_rate(self, s, coin: str):
        r = (await s.execute(text(
            "SELECT rate FROM strat_funding WHERE venue='binance' AND coin=:c "
            "ORDER BY ts DESC LIMIT 1"), {"c": coin})).first()
        return float(r[0]) if r and r[0] is not None else None

    async def _closes_1h(self, s, coin: str, limit: int = 300) -> list[float]:
        rows = (await s.execute(text(
            "SELECT c FROM strat_candles WHERE coin=:c AND tf='1h' ORDER BY ts DESC LIMIT :n"),
            {"c": coin, "n": limit})).all()
        return [float(r[0]) for r in reversed(rows)]

    async def _latest_oracle_age(self, s, coin: str):
        """Last oracle UPDATE time. The in-process ws feed records the receive
        time of every activeAssetCtx message (the oracle stream); strat_oi_1m
        only stores a minute-floored sample, so reading it here made every
        minute look >10 s stale and blocked the regime permanently."""
        ws = getattr(getattr(self.feeds, "ws", None), "last_msg_ts", None) if self.feeds else None
        if ws:
            t = ws.get(f"ctx:{coin}") or ws.get(f"book:{coin}")
            if t:
                return int(t)
        r = (await s.execute(text(
            "SELECT ts FROM strat_book_5s WHERE coin=:c ORDER BY ts DESC LIMIT 1"), {"c": coin})).first()
        return int(r[0]) if r and r[0] is not None else None

    async def _spread_inputs(self, s, coin: str, now_ms: int):
        """Latest quote spread and its 24h median from strat_book_5s (D-108).

        Both are returned as None until the column has data, which the regime
        gate reports as `spread_unavailable` — the same honest 'unknown' it used
        before, but now it can actually become known."""
        last = (await s.execute(text(
            "SELECT spread FROM strat_book_5s WHERE coin=:c AND spread IS NOT NULL "
            "ORDER BY ts DESC LIMIT 1"), {"c": coin})).first()
        if not last or last[0] is None:
            return None, None
        rows = (await s.execute(text(
            "SELECT spread FROM strat_book_5s WHERE coin=:c AND spread IS NOT NULL "
            "AND ts > :a AND ts <= :b ORDER BY spread"),
            {"c": coin, "a": now_ms - 86_400_000, "b": now_ms})).all()
        vals = [float(r[0]) for r in rows if r[0] is not None]
        if len(vals) < 60:                     # too thin to have a meaningful median
            return float(last[0]), None
        mid = len(vals) // 2
        median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
        return float(last[0]), median

    async def _regime_inputs(self, s, coin: str, now_ms: int):
        """Regime score inputs from what exists: RV last-1h vs 24h (15m closes),
        |OI 24h change| (OI tape, shorter window while warming), funding
        extremity = min(1, |z|/2) (z=2 is the doc's EXTREME line)."""
        from .strategies import common as cm
        rows = (await s.execute(text(
            "SELECT c FROM strat_candles WHERE coin=:c AND tf='15m' ORDER BY ts DESC LIMIT 100"),
            {"c": coin})).all()
        closes = [float(r[0]) for r in reversed(rows)]
        rv_ratio, _w = cm.rv_1h_vs_24h(closes)
        oi_rows = (await s.execute(text(
            "SELECT ts, oi_notional FROM strat_oi_1m WHERE coin=:c AND ts>=:a ORDER BY ts"),
            {"c": coin, "a": now_ms - 25 * 3_600_000})).mappings().all()
        chg, _have, _w2 = cm.oi_change([dict(r) for r in oi_rows], 24 * 3_600_000, now_ms)
        oi_mag = abs(chg) if chg is not None else None
        gr = (await s.execute(text(
            "SELECT funding_z FROM strat_gauge WHERE coin=:c ORDER BY ts DESC LIMIT 1"), {"c": coin})).first()
        f_ext = min(1.0, abs(float(gr[0])) / 2.0) if gr and gr[0] is not None else None
        return rv_ratio, oi_mag, f_ext

    async def _upcoming_events(self, s, now_ms: int) -> list[int]:
        rows = (await s.execute(text(
            "SELECT utc_ts FROM strat_events WHERE utc_ts BETWEEN :a AND :b"),
            {"a": now_ms - 30 * _MIN_MS, "b": now_ms + 2 * 3_600_000})).all()
        return [int(r[0]) for r in rows]

    async def tick(self) -> None:
        now_ms = int(time.time() * 1000)
        minute = now_ms // _MIN_MS
        async with self._sf() as s:
            # heartbeat first (always honest, even with empty feeds)
            health = await self._feed_health(s)
            hb = " | ".join(
                f"{k}:{v['rows']}rows" + (f"/{v['age_s']:.0f}s" if v["age_s"] is not None else "/none")
                for k, v in health.items())
            logger.info("heartbeat | %s", hb)

            for coin in self._assets:
                # --- funding gauge, per minute (always written; a short funding
                # history is reported as crowding_level='insufficient_data' with
                # whatever z is computable — never skipped) ---
                if self._last_gauge_min.get(coin) != minute:
                    hlf = await self._latest_hl_funding(s, coin)
                    g = fg.compute_gauge(hlf, await self._hist_8h_equiv(s, coin), await self._latest_binance_rate(s, coin))
                    prev = (await s.execute(text(
                        "SELECT crowding_level FROM strat_gauge WHERE coin=:c ORDER BY ts DESC LIMIT 1"),
                        {"c": coin})).first()
                    prev_level = prev[0] if prev else None
                    s.add(StratGauge(ts=now_ms, coin=coin, hl_funding_8h_equiv=g["hl_funding_8h_equiv"],
                                     funding_z=g["funding_z"], crowding_level=g["crowding_level"],
                                     tilt=g["tilt"], blocked_direction=g["blocked_direction"]))
                    # Telegram-outbox alert on crowding-level change (doc 02 §13)
                    if (prev_level is not None and prev_level != g["crowding_level"]
                            and g["crowding_level"] != "insufficient_data"):
                        await self._outbox_gauge_change(s, coin, prev_level, g, now_ms)
                    self._last_gauge_min[coin] = minute

                # --- regime + bias (regime is time/event based; bias from what exists) ---
                closes_1h = await self._closes_1h(s, coin)
                if True:
                    rv_ratio, oi_mag, f_ext = await self._regime_inputs(s, coin, now_ms)
                    # D-108: the spread pair was never passed, so `spread_wide`
                    # could not fire and every row reported `spread_unavailable`.
                    spread, med_spread = await self._spread_inputs(s, coin, now_ms)
                    r = regime_mod.evaluate(
                        now_ms, event_ts_list=await self._upcoming_events(s, now_ms),
                        last_oracle_ms=await self._latest_oracle_age(s, coin),
                        spread=spread, median_24h_spread=med_spread,
                        rv_vs_24h_avg=rv_ratio, oi_24h_change_mag=oi_mag, funding_extremity=f_ext)
                    s.add(StratRegime(ts=now_ms, coin=coin, allowed=r["allowed"], score=r["score"], blockers=r["blockers"]))

                    if self._last_bias_5min.get(coin) != minute // 5:
                        # hull_direction returns 0 (flat) while the Hull is warming — evaluated, not skipped
                        hull_dir = timing_mod.hull_direction(closes_1h, 21) if closes_1h else None
                        g_tilt = None
                        gr = (await s.execute(text(
                            "SELECT tilt FROM strat_gauge WHERE coin=:c ORDER BY ts DESC LIMIT 1"), {"c": coin})).first()
                        if gr and gr[0] is not None:
                            g_tilt = float(gr[0])
                        b = bias_mod.compute_bias(funding_tilt=g_tilt, hull_1h_dir=hull_dir)
                        s.add(StratBias(ts=now_ms, coin=coin, bias_score=b["bias_score"], components=b["components"]))
                        self._last_bias_5min[coin] = minute // 5

            await self._retention_if_due(s, now_ms)
            await s.commit()

        # Strategy evaluation (paper only — every strategy, every cadence, no gates).
        # candle_15m strategies run on a 15m boundary; minute strategies every tick.
        cur_15m = now_ms // 900_000
        on_boundary = cur_15m != self._last_15m
        self._last_15m = cur_15m
        try:
            await self._evaluator.run(on_candle_boundary=on_boundary)
        except Exception:  # noqa: BLE001 — evaluation faults never stop the data layer
            logger.exception("evaluator run failed")
        try:
            await self._models.run(now_ms)
        except Exception:  # noqa: BLE001 — model faults never stop 01–06 or the data layer
            logger.exception("model runner failed")

    async def _outbox_gauge_change(self, s, coin: str, prev_level: str, g: dict, now_ms: int) -> None:
        z = g.get("funding_z")
        z_txt = f" (z={z:.1f})" if z is not None else ""
        blk = f" -> {g['blocked_direction']} entries blocked" if g.get("blocked_direction") else ""
        msg = (f"[S02 GAUGE] {coin} funding {g['crowding_level']}{z_txt} "
               f"(was {prev_level}){blk}")
        hour_bucket = now_ms // 3_600_000
        dedupe = f"gauge:{coin}:{g['crowding_level']}:{hour_bucket}"
        stmt = mysql_insert(StratTelegramOutbox).values(
            ts=now_ms, kind="gauge_level_change", strategy_id="s02_funding_flow",
            coin=coin, message=msg, dedupe_key=dedupe, sent=False)
        stmt = stmt.on_duplicate_key_update(ts=now_ms)   # idempotent per hour bucket
        await s.execute(stmt)
        logger.info("outbox: %s", msg)

    async def _retention_if_due(self, s, now_ms: int) -> None:
        """Daily retention (prompt Part 2/3). 15m candles 18 months; book_5s 30
        days; trades_1m 18 months. OI/funding/liquidations kept (cannot re-obtain).
        Logs what it pruned. No-op while feeds are empty."""
        day = now_ms // 86_400_000
        if day == self._last_retention_day:
            return
        self._last_retention_day = day
        cutoffs = {
            "strat_candles WHERE tf='15m'": now_ms - 548 * 86_400_000,  # ~18 months
            "strat_trades_1m": now_ms - 548 * 86_400_000,
            "strat_book_5s": now_ms - 30 * 86_400_000,
        }
        for table_where, cutoff in cutoffs.items():
            res = await s.execute(text(f"DELETE FROM {table_where.split(' WHERE ')[0]} WHERE "
                                       + (table_where.split(' WHERE ')[1] + " AND " if ' WHERE ' in table_where else "")
                                       + "ts < :cut"), {"cut": cutoff})
            if res.rowcount:
                logger.info("retention pruned %d rows from %s", res.rowcount, table_where)
