"""FeedManager (prompt Part 2). Orchestrates the data layer inside the worker.

Starts: one-time backfills (candleSnapshot 15m/1h/4h + 30-day fundingHistory),
the market-wide HL ws feed (0 user slots — asserted), and the CEX funding poll
loop (Binance premiumIndex + Bybit tickers, verified reachable). Tracks an HL/CEX
request budget. Retention runs in the scheduler.

Liquidations (coverage='partial'): `ingest_liquidation_fill()` writes a
liquidation-flagged fill to strat_liquidations. It is fed by the EXISTING
<=10-slot ws tier (the copy tracker); the one-line hookup into that tracker is
deliberately NOT made here (that path is money-adjacent and off-limits for
unattended work — see report). No rows are fabricated; the table stays honestly
empty until the hookup lands.
"""
from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import text
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.strategy_models import StratCandle, StratFunding, StratLiquidation
from . import hl_info, cex_funding
from .hl_ws import HLMarketFeed

logger = logging.getLogger("strategy_engine.feeds")

_CEX_POLL_S = 60
_INTERVALS = ["15m", "1h", "4h"]
_INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}
_DAY_MS = 86_400_000


class FeedManager:
    def __init__(self, session_factory, coins: list[str]) -> None:
        self._sf = session_factory
        self._coins = coins
        self.ws = HLMarketFeed(session_factory, coins)
        self._tasks: list[asyncio.Task] = []
        # request-budget counters (per process lifetime)
        self.req = {"hl_info_backfill": 0, "cex_binance": 0, "cex_bybit": 0}
        self.cex_poll_interval_s = _CEX_POLL_S

    # ---- D-105: stall watchdog -------------------------------------------------
    WATCHDOG_TICK_S = 60
    STALL_MS = 30 * 60 * 1000        # no rows for 30 min while the market trades

    # (table, ts column, label) — the feeds whose silence is a fault, not a lull.
    WATCHED = (
        ("strat_candles", "ts", "candles"),
        ("strat_oi_1m", "ts", "oi"),
        ("strat_trades_1m", "ts", "taker"),
        ("strat_book_5s", "ts", "book"),
    )

    async def _watchdog(self, stop_event: asyncio.Event) -> None:
        """Restart the market websocket if any watched feed writes no row for 30
        minutes. Crypto trades 24/7, so there is no session in which silence is
        normal — a quiet tape still produces candle and book rows.

        D-105 (2026-09-11): the live liquidation feed went silent for 1,461
        minutes from 2026-09-04 16:36 and nothing noticed; the gap only surfaced
        in a report six days later."""
        from sqlalchemy import text as _text
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.WATCHDOG_TICK_S)
                return
            except asyncio.TimeoutError:
                pass
            try:
                now_ms = int(time.time() * 1000)
                stalled = []
                async with self._sf() as s:
                    for table, col, label in self.WATCHED:
                        newest = (await s.execute(_text(
                            f"SELECT MAX({col}) FROM {table} WHERE coin IN :c"),
                            {"c": tuple(self._coins)})).scalar()
                        if newest is None or now_ms - int(newest) > self.STALL_MS:
                            age = "never" if newest is None else f"{(now_ms - int(newest)) / 60000:.0f}m"
                            stalled.append(f"{label}={age}")
                if stalled:
                    logger.error("FEED WATCHDOG: stalled feeds %s — restarting the market ws",
                                 ", ".join(stalled))
                    # HLMarketFeed has no stop() — cancelling the task is the
                    # restart mechanism; run() re-subscribes from scratch.
                    for t in list(self._tasks):
                        if t.get_name() == "hl_ws" and not t.done():
                            t.cancel()
                    self._tasks = [t for t in self._tasks if t.get_name() != "hl_ws"]
                    self._tasks.append(asyncio.create_task(self.ws.run(stop_event), name="hl_ws"))
                    async with self._sf() as s:
                        await s.execute(_text(
                            "INSERT INTO strat_telegram_outbox (ts, kind, strategy_id, coin, message, dedupe_key, sent) "
                            "VALUES (:ts,'feed_stale','feeds','-',:m,:k,0) "
                            "ON DUPLICATE KEY UPDATE ts=VALUES(ts)"),
                            {"ts": now_ms,
                             "m": f"Feed watchdog restarted the market websocket — stalled: {', '.join(stalled)}",
                             "k": f"feed_stall:{now_ms // (30 * 60 * 1000)}"})
                        await s.commit()
            except Exception as e:  # noqa: BLE001 — the watchdog must never die
                logger.warning("feed watchdog cycle failed: %s", e)

    async def start(self, stop_event: asyncio.Event) -> None:
        await self._backfill_once()
        self._tasks = [
            asyncio.create_task(self.ws.run(stop_event), name="hl_ws"),
            asyncio.create_task(self._cex_loop(stop_event), name="cex"),
            asyncio.create_task(self._watchdog(stop_event), name="watchdog"),
        ]
        # ASSERT market-wide only — zero user slots consumed by the strategy feed.
        assert self.ws.user_slot_subscriptions == 0, "strategy feed must consume 0 user slots"
        logger.info("feeds started | coins=%s | cex_poll=%ds | user_slots=%d",
                    self._coins, self.cex_poll_interval_s, self.ws.user_slot_subscriptions)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()

    def budget_snapshot(self) -> dict:
        """Projected HL/CEX requests per hour (ws consumes no REST budget)."""
        per_hr_cex = (3600 / self.cex_poll_interval_s) * len(self._coins) * 2  # binance + bybit
        # deterministic count: per coin = trades + l2Book + activeAssetCtx + 3 candle tfs = 6
        expected_subs = len(self._coins) * 6
        return {
            "ws_subscriptions_marketwide": expected_subs,
            "ws_subscriptions_live": self.ws.subscription_count,
            "ws_user_slots": self.ws.user_slot_subscriptions,
            "cex_requests_per_hour": int(per_hr_cex),
            "hl_rest_per_hour": 0,   # ws-driven; backfills are one-time
            "totals_since_start": dict(self.req),
        }

    async def _backfill_once(self) -> None:
        """candleSnapshot (as deep as served) + 30-day fundingHistory. Records the
        depth obtained. Idempotent via upsert."""
        now = hl_info.now_ms()
        async with self._sf() as s:
            for coin in self._coins:
                for iv in _INTERVALS:
                    # ~18 months for 15m where available; endpoint decides real depth
                    span = 548 * _DAY_MS if iv == "15m" else 730 * _DAY_MS
                    rows = await hl_info.candle_snapshot(coin, iv, now - span, now)
                    self.req["hl_info_backfill"] += 1
                    for c in rows:
                        stmt = mysql_insert(StratCandle).values(
                            venue="hl", coin=coin, tf=iv, ts=c["close_ts"],
                            o=c["o"], h=c["h"], l=c["l"], c=c["c"], v=c["v"])
                        stmt = stmt.on_duplicate_key_update(o=c["o"], h=c["h"], l=c["l"], c=c["c"], v=c["v"])
                        await s.execute(stmt)
                    logger.info("backfill %s %s: %d candles (%s..%s)", coin, iv, len(rows),
                                rows[0]["close_ts"] if rows else "-", rows[-1]["close_ts"] if rows else "-")
                # 30-day HL funding history
                fh = await hl_info.funding_history(coin, now - 30 * _DAY_MS, now)
                self.req["hl_info_backfill"] += 1
                for f in fh:
                    stmt = mysql_insert(StratFunding).values(
                        venue="hl", coin=coin, ts=f["ts"], rate=f["rate"], predicted_rate=f["rate"])
                    stmt = stmt.on_duplicate_key_update(rate=f["rate"])
                    await s.execute(stmt)
                logger.info("backfill %s funding: %d rows", coin, len(fh))
                await s.commit()   # commit per coin so partial progress survives a restart
            await s.commit()

    async def _cex_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            async with self._sf() as s:
                for coin in self._coins:
                    b = await cex_funding.poll_binance(coin)
                    if b:
                        self.req["cex_binance"] += 1
                        await self._upsert_funding(s, b)
                    y = await cex_funding.poll_bybit(coin)
                    if y:
                        self.req["cex_bybit"] += 1
                        await self._upsert_funding(s, y)
                await s.commit()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.cex_poll_interval_s)
            except asyncio.TimeoutError:
                pass

    async def _upsert_funding(self, s, row: dict) -> None:
        stmt = mysql_insert(StratFunding).values(**row)
        stmt = stmt.on_duplicate_key_update(
            rate=row["rate"], predicted_rate=row["predicted_rate"],
            next_settlement_ts=row["next_settlement_ts"], oi_notional=row["oi_notional"])
        await s.execute(stmt)

    async def ingest_liquidation_fill(self, fill: dict) -> None:
        """Write a liquidation-flagged fill (coverage='partial'). Called by the
        existing <=10-slot ws tier when wired (Phase 2). Never fabricates."""
        async with self._sf() as s:
            s.add(StratLiquidation(
                ts=int(fill["ts"]), coin=fill["coin"], side=fill.get("side"),
                px=fill.get("px"), sz=fill.get("sz"), notional=fill.get("notional"),
                liquidated_user=fill.get("liquidated_user"), mark_px=fill.get("mark_px"),
                method=fill.get("method"), coverage="partial"))
            await s.commit()

    def cex_backoff(self) -> None:
        """Budget guard: widen CEX poll cadence 60s -> 120s (prompt budget guard)."""
        self.cex_poll_interval_s = min(self.cex_poll_interval_s * 2, 300)
        logger.warning("CEX poll backed off to %ds", self.cex_poll_interval_s)
