"""Wallet trade insights — reconstruct closed round-trips for every tracked
wallet from `leader_trade_events`, price the exit legs from Perpl 5-min candles,
and persist them to `wallet_trade_insights` for the Insights page.

Method (validated offline 2026-07-31 against manual reconstruction):
  * events per (wallet, market, side): 'opened' starts a span, 'closed' ends it;
    a 'closed' with no live span means tracking began mid-position (open_t NULL).
  * `size` on events is the NEW TOTAL position size (not a delta); `price` is the
    avg entry snapshot. A 'reduced' leg realizes (prev_total - new_total) at the
    candle close of that minute vs the avg entry at that time.
  * detected_at is UTC (verified vs UTC_TIMESTAMP) — candle ts are UTC ms.
  * est_pnl is GROSS of fees/rebates and candle-priced — an estimate, not fills.
  * Leverage is not reconstructable: events never recorded lv/deposit.
"""
import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import text

from app.config import settings
from app.db.database import get_session_factory
from app.services.perpl_client import perpl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

CANDLE_RES = 300  # 5-min candles
REFRESH_INTERVAL = 6 * 3600
_state = {"running": False, "last_refresh": None, "last_error": None, "trades": 0}


def status() -> dict:
    return dict(_state)


def _utc_ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


async def _price_decimals() -> dict[int, int]:
    ctx = await perpl_client.get_context()
    out = {}
    for m in ctx.get("markets", []):
        cfg = m.get("config", m)
        pd = cfg.get("price_decimals", cfg.get("priceDecimals"))
        if m.get("id") is not None and pd is not None:
            out[int(m["id"])] = int(pd)
    return out


async def _fetch_candles(mid: int, res: int, frm: int, to: int, scale: int) -> dict[int, float]:
    """Fetch [frm, to] ms range in <=1000-candle chunks. Missing chunks are
    skipped (legs there stay unpriced rather than mispriced)."""
    out: dict[int, float] = {}
    step = res * 1000 * 1000
    cur = frm
    while cur < to:
        end = min(cur + step, to)
        try:
            d = await perpl_client._get_json(f"/api/v1/market-data/{mid}/candles/{res}/{cur}-{end}")
            for c in d.get("d", []):
                out[int(c["t"])] = float(c["c"]) / scale
        except Exception as exc:
            logger.warning("insights: candles %s/%s %s-%s failed: %s", mid, res, cur, end, exc)
        await asyncio.sleep(0.3)
        cur = end
    return out


async def refresh() -> int:
    """Full rebuild of wallet_trade_insights. Returns row count written."""
    if _state["running"]:
        return -1
    _state["running"] = True
    _state["last_error"] = None
    try:
        n = await _refresh_inner()
        _state["last_refresh"] = datetime.utcnow().isoformat()
        _state["trades"] = n
        return n
    except Exception as exc:
        _state["last_error"] = str(exc)
        logger.error("insights refresh failed: %s", exc)
        raise
    finally:
        _state["running"] = False


async def _refresh_inner() -> int:
    sf = get_session_factory()
    async with sf() as session:
        res = await session.execute(text(
            "SELECT trader_wallet, detected_at, market_id, symbol, side, event_type, size, price, "
            "  JSON_EXTRACT(raw, '$.leverage') "
            "FROM leader_trade_events ORDER BY trader_wallet, detected_at, id"
        ))
        rows = res.fetchall()
    logger.info("insights: %d events loaded", len(rows))

    # --- reconstruct closed spans per wallet ---
    wallet_spans: dict[str, list] = defaultdict(list)
    open_span: dict[tuple, dict | None] = {}
    for w, t, mid, sym, side, et, size, price, lev in rows:
        size = float(size) if size is not None else None
        price = float(price) if price is not None else None
        lev = float(lev) if lev is not None else None
        k = (w, mid, side)
        s = open_span.get(k)
        if et == "opened":
            open_span[k] = {"mid": mid, "sym": sym, "side": side, "open_t": t,
                            "events": [(t, et, size, price)], "max_size": size or 0.0,
                            "leverage": lev}
        elif et == "closed":
            if s is None:
                s = {"mid": mid, "sym": sym, "side": side, "open_t": None,
                     "events": [], "max_size": size or 0.0, "leverage": None}
            s["events"].append((t, et, size, price))
            if lev is not None:
                s["leverage"] = lev
            s["close_t"] = t
            wallet_spans[w].append(s)
            open_span[k] = None
        elif s is not None:
            s["events"].append((t, et, size, price))
            if lev is not None:
                s["leverage"] = lev
            if et == "increased" and size:
                s["max_size"] = max(s["max_size"], size)

    # --- candle windows needed per market ---
    need: dict[int, set] = defaultdict(set)
    for spans in wallet_spans.values():
        for s in spans:
            for t, et, size, price in s["events"]:
                if et in ("reduced", "closed"):
                    ts = _utc_ms(t)
                    need[s["mid"]].add(ts - ts % (CANDLE_RES * 1000))

    pdec = await _price_decimals()
    candles: dict[int, dict[int, float]] = defaultdict(dict)
    for mid, mins in need.items():
        if mid not in pdec:
            continue  # delisted market with no live config — legs stay unpriced
        ts_sorted = sorted(mins)
        ranges, start, prev = [], ts_sorted[0], ts_sorted[0]
        for ts in ts_sorted[1:]:
            if ts - prev > 900 * CANDLE_RES * 1000:
                ranges.append((start, prev))
                start = ts
            prev = ts
        ranges.append((start, prev))
        for a, b in ranges:
            got = await _fetch_candles(mid, CANDLE_RES, a - 2 * CANDLE_RES * 1000,
                                       b + CANDLE_RES * 1000, 10 ** pdec[mid])
            candles[mid].update(got)

    def px_at(mid: int, t: datetime) -> float | None:
        ts = _utc_ms(t)
        m = ts - ts % (CANDLE_RES * 1000)
        for back in range(4):
            p = candles[mid].get(m - back * CANDLE_RES * 1000)
            if p is not None:
                return p
        return None

    # --- price spans, build rows ---
    out_rows = []
    for w, spans in wallet_spans.items():
        for s in spans:
            d = 1 if s["side"] == "long" else -1
            pnl = 0.0
            legs = 0
            prev_total = avg_entry = None
            for t, et, size, price in s["events"]:
                if et in ("opened", "increased"):
                    prev_total, avg_entry = size, price
                elif et == "reduced":
                    delta = (prev_total or 0) - (size or 0)
                    xp = px_at(s["mid"], t)
                    if xp is not None and delta > 0 and avg_entry:
                        pnl += d * delta * (xp - avg_entry)
                        legs += 1
                    prev_total, avg_entry = size, price
                elif et == "closed":
                    xp = px_at(s["mid"], t)
                    ae = avg_entry if avg_entry is not None else price
                    if xp is not None and size and ae:
                        pnl += d * size * (xp - ae)
                        legs += 1
                    if ae is not None:
                        avg_entry = ae
            hold = int((s["close_t"] - s["open_t"]).total_seconds()) if s["open_t"] else None
            notional = (s["max_size"] * avg_entry) if (s["max_size"] and avg_entry) else None
            out_rows.append({
                "wallet": w, "market_id": s["mid"], "symbol": s["sym"], "side": s["side"],
                "open_t": s["open_t"], "close_t": s["close_t"], "hold_sec": hold,
                "max_size": s["max_size"] or None, "avg_entry": avg_entry,
                "notional_usd": notional, "leverage": s.get("leverage"),
                "est_pnl": round(pnl, 4) if legs else None, "legs": legs,
            })

    # --- rewrite table ---
    async with sf() as session:
        await session.execute(text("DELETE FROM wallet_trade_insights"))
        if out_rows:
            ins = text(
                "INSERT INTO wallet_trade_insights "
                "(wallet, market_id, symbol, side, open_t, close_t, hold_sec, max_size, "
                " avg_entry, notional_usd, leverage, est_pnl, legs, computed_at) "
                "VALUES (:wallet, :market_id, :symbol, :side, :open_t, :close_t, :hold_sec, "
                " :max_size, :avg_entry, :notional_usd, :leverage, :est_pnl, :legs, UTC_TIMESTAMP())"
            )
            for i in range(0, len(out_rows), 500):
                await session.execute(ins, out_rows[i:i + 500])
        await session.commit()
    logger.info("insights: wrote %d trades", len(out_rows))
    return len(out_rows)


_task: asyncio.Task | None = None


async def _loop():
    await asyncio.sleep(120)  # let startup settle
    while True:
        try:
            await refresh()
        except Exception:
            pass
        await asyncio.sleep(REFRESH_INTERVAL)


async def start() -> None:
    global _task
    # D-79: the refresh loads ALL leader_trade_events (4.5M rows, one wallet = 3.3M)
    # into memory and OOM-killed the API every ~18 min on prod (2026-09-06). Gate the
    # background refresh on WALLET_INSIGHTS_ENABLED; the page keeps serving stored rows.
    if not settings.WALLET_INSIGHTS_ENABLED:
        logger.info("Wallet insights service disabled (WALLET_INSIGHTS_ENABLED=false); stored rows still served")
        return
    _task = asyncio.create_task(_loop())
    logger.info("Wallet insights service started (refresh every %dh)", REFRESH_INTERVAL // 3600)


async def stop() -> None:
    if _task:
        _task.cancel()
