"""Hyperliquid MARKET-WIDE websocket feed (doc 00 §3, prompt Part 2).

One outbound ws to wss://api.hyperliquid.xyz/ws. Subscriptions are market-wide
only — trades, l2Book, candle (15m/1h/4h), activeAssetCtx — for the configured
coins. **No userFills**, so ZERO user slots are consumed (asserted in feeds.py).

- candle     -> upsert strat_candles (keyed venue,coin,tf,close_ts)
- trades     -> in-memory 1-minute taker buy/sell notional -> strat_trades_1m
                (raw ticks are NOT stored)
- l2Book     -> sampled every 5 s: notional depth within 0.1/0.3/0.5% of mid
                -> strat_book_5s
- activeAssetCtx -> latest OI/funding/mark/oracle/premium per coin, flushed per
                minute -> strat_oi_1m (+ an 'hl' row into strat_funding)

Reconnect with backoff (mirrors services/hyperliquid/prices.py). Nothing trades.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import websockets
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.strategy_models import StratCandle, StratTrades1m, StratBook5s, StratOi1m, StratFunding

logger = logging.getLogger("strategy_engine.hl_ws")

WS_URL = "wss://api.hyperliquid.xyz/ws"
_TF_BY_INTERVAL = {"15m": "15m", "1h": "1h", "4h": "4h"}
_INTERVALS = ["15m", "1h", "4h"]
_BOOK_SAMPLE_S = 5
_MIN_MS = 60_000


class HLMarketFeed:
    def __init__(self, session_factory, coins: list[str]) -> None:
        self._sf = session_factory
        self._coins = coins
        self._trade_min: dict[tuple[str, int], dict] = {}   # (coin, minute_ms) -> agg
        self._ctx_latest: dict[str, dict] = {}
        self._book_pending: list[dict] = []
        self._last_book_sample: dict[str, float] = {}
        self.msg_count = 0
        self.last_msg_ts: dict[str, int] = {}
        self.subscription_count = 0
        self.user_slot_subscriptions = 0                    # MUST stay 0

    # ---- subscriptions (market-wide only) ----
    def _subscriptions(self) -> list[dict]:
        subs = []
        for coin in self._coins:
            subs.append({"type": "trades", "coin": coin})
            subs.append({"type": "l2Book", "coin": coin})
            subs.append({"type": "activeAssetCtx", "coin": coin})
            for iv in _INTERVALS:
                subs.append({"type": "candle", "coin": coin, "interval": iv})
        return subs

    async def run(self, stop_event: asyncio.Event) -> None:
        flusher = asyncio.create_task(self._flush_loop(stop_event))
        backoff = 1.0
        try:
            while not stop_event.is_set():
                try:
                    async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10, max_size=8_000_000) as ws:
                        subs = self._subscriptions()
                        for sub in subs:
                            assert sub["type"] != "userFills", "market-wide only — userFills is banned (user slots)"
                            await ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
                        self.subscription_count = len(subs)
                        logger.info("HL market feed subscribed: %d market-wide subs (user slots used: %d)",
                                    self.subscription_count, self.user_slot_subscriptions)
                        backoff = 1.0
                        async for raw in ws:
                            if stop_event.is_set():
                                break
                            try:
                                self._dispatch(json.loads(raw))
                            except Exception:  # noqa: BLE001
                                logger.debug("bad ws message", exc_info=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("HL market ws dropped (%s) — reconnecting in %.0fs", exc, backoff)
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                    except asyncio.TimeoutError:
                        pass
                    backoff = min(backoff * 2, 30.0)
        finally:
            flusher.cancel()
            await self._flush(final=True)

    # ---- message dispatch ----
    def _dispatch(self, msg: dict) -> None:
        ch = msg.get("channel")
        data = msg.get("data")
        if ch == "candle":
            self._on_candle(data)
        elif ch == "trades":
            self._on_trades(data)
        elif ch == "l2Book":
            self._on_book(data)
        elif ch == "activeAssetCtx":
            self._on_ctx(data)
        else:
            return
        self.msg_count += 1

    def _on_candle(self, d: dict) -> None:
        coin = d.get("s")
        iv = _TF_BY_INTERVAL.get(d.get("i"))
        if not coin or not iv:
            return
        self._candle_upsert = getattr(self, "_candle_upsert", [])
        self._candle_upsert.append({
            "venue": "hl", "coin": coin, "tf": iv, "ts": int(d["T"]),
            "o": float(d["o"]), "h": float(d["h"]), "l": float(d["l"]), "c": float(d["c"]), "v": float(d["v"]),
        })
        self.last_msg_ts[f"candle:{coin}"] = int(time.time() * 1000)

    def _on_trades(self, arr) -> None:
        now = int(time.time() * 1000)
        for t in (arr or []):
            coin = t.get("coin")
            if not coin:
                continue
            px = float(t["px"]); sz = float(t["sz"])
            minute = (int(t.get("time", now)) // _MIN_MS) * _MIN_MS
            key = (coin, minute)
            agg = self._trade_min.setdefault(key, {"buy": 0.0, "sell": 0.0, "count": 0})
            # HL trade side: 'B' = aggressor bought (taker buy), 'A' = taker sell
            if t.get("side") == "B":
                agg["buy"] += px * sz
            else:
                agg["sell"] += px * sz
            agg["count"] += 1
            self.last_msg_ts[f"trades:{coin}"] = now

    def _on_book(self, d: dict) -> None:
        coin = d.get("coin")
        levels = d.get("levels")
        if not coin or not levels or len(levels) < 2:
            return
        now = time.time()
        if now - self._last_book_sample.get(coin, 0) < _BOOK_SAMPLE_S:
            return
        self._last_book_sample[coin] = now
        bids, asks = levels[0], levels[1]
        if not bids or not asks:
            return
        best_bid = float(bids[0]["px"]); best_ask = float(asks[0]["px"])
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return

        def depth(side_levels, within):
            lo, hi = mid * (1 - within), mid * (1 + within)
            tot = 0.0
            for lv in side_levels:
                px = float(lv["px"]); sz = float(lv["sz"])
                if lo <= px <= hi:
                    tot += px * sz
            return tot

        # D-108: best_bid/best_ask were computed here and DISCARDED, so the regime
        # gate's `spread_wide` condition could never fire — it reported
        # `spread_unavailable` on 100% of 33,166 rows. The quote spread is stored
        # from now on; nothing about the gate's threshold changes.
        self._book_pending.append({
            "coin": coin, "ts": int(now * 1000), "mid": mid,
            "spread": best_ask - best_bid,
            "bid_0_1": depth(bids, 0.001), "bid_0_3": depth(bids, 0.003), "bid_0_5": depth(bids, 0.005),
            "ask_0_1": depth(asks, 0.001), "ask_0_3": depth(asks, 0.003), "ask_0_5": depth(asks, 0.005),
        })
        self.last_msg_ts[f"book:{coin}"] = int(now * 1000)

    def _on_ctx(self, d: dict) -> None:
        coin = d.get("coin")
        ctx = d.get("ctx") or {}
        if not coin:
            return
        mark = _f(ctx.get("markPx"))
        oi = _f(ctx.get("openInterest"))
        self._ctx_latest[coin] = {
            "funding": _f(ctx.get("funding")),
            "mark": mark, "oracle": _f(ctx.get("oraclePx")), "premium": _f(ctx.get("premium")),
            "oi_notional": (oi * mark if (oi is not None and mark is not None) else None),
        }
        self.last_msg_ts[f"ctx:{coin}"] = int(time.time() * 1000)

    # ---- periodic persistence ----
    async def _flush_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_BOOK_SAMPLE_S)
            except asyncio.TimeoutError:
                pass
            try:
                await self._flush()
            except Exception:  # noqa: BLE001
                logger.exception("flush failed")

    async def _flush(self, final: bool = False) -> None:
        now = int(time.time() * 1000)
        cur_minute = (now // _MIN_MS) * _MIN_MS
        async with self._sf() as s:
            # candles (upsert)
            candles = getattr(self, "_candle_upsert", [])
            self._candle_upsert = []
            for c in candles:
                stmt = mysql_insert(StratCandle).values(**c)
                stmt = stmt.on_duplicate_key_update(o=c["o"], h=c["h"], l=c["l"], c=c["c"], v=c["v"])
                await s.execute(stmt)
            # completed trade minutes
            done = [k for k in self._trade_min if k[1] < cur_minute or final]
            for key in done:
                coin, minute = key
                agg = self._trade_min.pop(key)
                stmt = mysql_insert(StratTrades1m).values(
                    coin=coin, ts=minute, taker_buy_notional=agg["buy"],
                    taker_sell_notional=agg["sell"], count=agg["count"])
                stmt = stmt.on_duplicate_key_update(
                    taker_buy_notional=agg["buy"], taker_sell_notional=agg["sell"], count=agg["count"])
                await s.execute(stmt)
            # book samples
            for b in self._book_pending:
                s.add(StratBook5s(**b))
            self._book_pending = []
            # OI/funding per minute
            for coin, ctx in list(self._ctx_latest.items()):
                stmt = mysql_insert(StratOi1m).values(
                    coin=coin, ts=cur_minute, oi_notional=ctx["oi_notional"], funding=ctx["funding"],
                    predicted_funding=ctx["funding"], mark=ctx["mark"], oracle=ctx["oracle"], premium=ctx["premium"])
                stmt = stmt.on_duplicate_key_update(
                    oi_notional=ctx["oi_notional"], funding=ctx["funding"], predicted_funding=ctx["funding"],
                    mark=ctx["mark"], oracle=ctx["oracle"], premium=ctx["premium"])
                await s.execute(stmt)
                # hl funding row (rate = current hourly funding; predicted proxy = same, doc 02)
                fstmt = mysql_insert(StratFunding).values(
                    venue="hl", coin=coin, ts=cur_minute, rate=ctx["funding"],
                    predicted_rate=ctx["funding"], oi_notional=ctx["oi_notional"])
                fstmt = fstmt.on_duplicate_key_update(rate=ctx["funding"], predicted_rate=ctx["funding"], oi_notional=ctx["oi_notional"])
                await s.execute(fstmt)
            await s.commit()


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
