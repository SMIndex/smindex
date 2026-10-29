"""Perpl round trips from perpl_events (the Envio indexer's table), for
wallets whose indexed history is complete from their first trade.

The indexer is behind the chain (its newest block is read from the table
each run, never assumed). Until it is current, no Perpl wallet can pass the
"last fill within 14 days" rule, so Perpl rows are written with qualified=0
and flags.history says how far the index reaches; the page shows them as
"History incomplete" and never ranks them. The trips themselves are built
the same way as Hyperliquid's (open to flat per market) from the Position*
events, with fees from the order-fill events of the same transaction.

Units (verified on the live registry 2026-10-29): CNS = USD x 1e6,
LNS = size x 10^size_decimals, PNS = price x 10^price_decimals, per market.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from sqlalchemy import bindparam, text

from app.db.database import get_session_factory
from app.services.featured import roundtrips as rt
from app.utils.logger import get_logger

logger = get_logger(__name__)

POSITION_EVENTS = ("PositionIncreased", "PositionDecreased", "PositionClosed", "PositionInverted", "PositionLiquidated")
FILL_EVENTS = ("TakerOrderFilled", "MakerOrderFilled")
MAX_WALLETS = 300


async def _decimals() -> dict[int, tuple[int, int, str]]:
    """market_id -> (price_decimals, size_decimals, symbol) from the registry."""
    from app.services import market_registry
    out = {}
    for m in await market_registry.get_all_markets():
        pd = m.get("price_decimals")
        sd = m.get("size_decimals")
        if m.get("market_id") is not None and pd is not None and sd is not None:
            out[int(m["market_id"])] = (int(pd), int(sd), str(m.get("symbol") or m["market_id"]))
    return out


async def index_range() -> tuple[datetime | None, datetime | None]:
    sf = get_session_factory()
    async with sf() as s:
        hi = (await s.execute(text("SELECT at FROM perpl_events ORDER BY block DESC LIMIT 1"))).scalar()
        lo = (await s.execute(text("SELECT at FROM perpl_events ORDER BY block ASC LIMIT 1"))).scalar()
    return lo, hi


async def _accounts_for(address: str) -> list[int]:
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text("SELECT account_id FROM perpl_addr_map WHERE address=:a"), {"a": address})).scalars().all()
    return [int(r) for r in rows]


async def build_for_address(address: str, dec: dict[int, tuple[int, int, str]]) -> dict:
    """Trips for one Perpl address. Returns {"trips": [...], "complete": bool,
    "first_event_at": datetime | None, "n_events": int}."""
    address = address.lower()
    accts = await _accounts_for(address)
    if not accts:
        return {"trips": [], "complete": False, "first_event_at": None, "n_events": 0}
    sf = get_session_factory()
    async with sf() as s:
        q = text("SELECT at, block, log_index, tx, account_id, market_id, event_name, fee, raw FROM perpl_events "
                 "WHERE account_id IN :a AND event_name IN :e ORDER BY block, log_index").bindparams(
            bindparam("a", expanding=True), bindparam("e", expanding=True))
        rows = (await s.execute(q, {"a": accts, "e": list(POSITION_EVENTS + FILL_EVENTS + ("AccountCreated",))})).all()
    if not rows:
        return {"trips": [], "complete": False, "first_event_at": None, "n_events": 0}
    import asyncio
    return await asyncio.to_thread(_build_rows, address, rows, dec)


def _build_rows(address: str, rows: list, dec: dict[int, tuple[int, int, str]]) -> dict:
    """Pure part of build_for_address (runs in a worker thread: json of
    thousands of events stalled the event loop for 5 s on 2026-10-29)."""
    first_at = rows[0][0]
    complete = rows[0][6] == "AccountCreated"
    # fees of the fills in a tx, per (account, perp)
    fills_by_tx: dict[tuple, list[dict]] = {}
    for at, block, li, tx, acct, mkt, name, fee, raw in rows:
        if name in FILL_EVENTS:
            r = json.loads(raw) if isinstance(raw, str) else (raw or {})
            fills_by_tx.setdefault((tx, int(acct)), []).append(
                {"fee": float(fee or 0), "lot": int(r.get("lotLNS") or 0), "px": int(r.get("pricePNS") or 0),
                 "perp": r.get("perpId", mkt), "maker": name == "MakerOrderFilled"})
    trips: list[dict] = []
    open_: dict[tuple, dict] = {}
    for at, block, li, tx, acct, mkt, name, fee, raw in rows:
        if name not in POSITION_EVENTS:
            continue
        r = json.loads(raw) if isinstance(raw, str) else (raw or {})
        perp = int(r.get("perpId") or mkt or 0)
        pd, sd, sym = dec.get(perp, (6, 0, f"MKT-{perp}"))
        key = (int(acct), perp)
        t_ms = rt.dt_to_ms(at)
        side = "long" if int(r.get("positionType", 0)) == 0 else "short"
        px = (int(r.get("pricePNS") or 0) / (10 ** pd)) if r.get("pricePNS") else None
        start_lot = int(r.get("startLotLNS") or 0)
        end_lot = int(r.get("endLotLNS") or 0)
        pnl = float(r.get("deltaPnlCNS") or 0) / 1e6
        fund = float(r.get("fundingCNS") or 0) / 1e6
        fills = fills_by_tx.get((tx, int(acct)), [])
        fee_usd = sum(f["fee"] for f in fills if f["perp"] in (None, perp)) + (float(r.get("insFeeCNS") or 0) + float(r.get("protFeeCNS") or 0)) / 1e6
        fill_px = None
        fill_lots = sum(f["lot"] for f in fills if f["perp"] in (None, perp))
        if fill_lots:
            fill_px = sum(f["lot"] * f["px"] for f in fills if f["perp"] in (None, perp)) / fill_lots / (10 ** pd)
        maker = sum(1 for f in fills if f["maker"] and f["perp"] in (None, perp))
        px = px or fill_px or 0.0
        size_delta = abs(end_lot - start_lot) / (10 ** sd)
        cur = open_.get(key)

        def new_trip(side_: str, qty: float, px_: float) -> dict:
            t = rt._new_trip(sym, side_, t_ms)
            t["entry_qty"] = qty
            t["entry_notional"] = qty * px_
            t["n_fills"] = 1
            t["entry_taker"] = (maker == 0) if fills else None
            t["maker_fills"] = 1 if maker else 0
            t["fees"] = fee_usd
            t["volume_usd"] = qty * px_
            t["max_size"] = qty
            t["max_size_usd"] = qty * px_
            t["px_min"] = t["px_max"] = px_
            t["accounts"] = {address}
            return t

        def close_part(t: dict, qty: float, px_: float, pnl_: float, fund_: float, fee_: float, liq: bool = False) -> None:
            t["n_fills"] += 1
            t["n_closes"] += 1
            t["exit_qty"] += qty
            t["exit_notional"] += qty * px_
            t["pnl_gross"] += pnl_
            t["funding"] += fund_
            t["fees"] += fee_
            t["volume_usd"] += qty * px_
            t["px_min"] = min(t["px_min"], px_)
            t["px_max"] = max(t["px_max"], px_)
            if liq:
                t["liquidation"] = True

        if name == "PositionIncreased":
            if cur is None or start_lot == 0:
                open_[key] = new_trip(side, size_delta, px)
            else:
                avg = cur["entry_notional"] / cur["entry_qty"] if cur["entry_qty"] else px
                worse = px < avg if cur["side"] == "long" else px > avg
                cur["averaged_down" if worse else "added_to_winner"] = True
                cur["n_fills"] += 1
                cur["n_adds"] += 1
                cur["entry_qty"] += size_delta
                cur["entry_notional"] += size_delta * px
                cur["fees"] += fee_usd
                cur["volume_usd"] += size_delta * px
                cur["maker_fills"] += 1 if maker else 0
                cur["px_min"] = min(cur["px_min"], px)
                cur["px_max"] = max(cur["px_max"], px)
                cur["max_size"] = max(cur["max_size"], end_lot / (10 ** sd))
                cur["max_size_usd"] = max(cur["max_size_usd"], end_lot / (10 ** sd) * px)
        elif name in ("PositionDecreased", "PositionClosed", "PositionLiquidated"):
            if cur is None:
                continue          # reduces a position opened before the index
            qty = size_delta if name == "PositionDecreased" else max(cur["entry_qty"] - cur["exit_qty"], 0.0)
            close_part(cur, qty, px, pnl, fund, fee_usd, liq=(name == "PositionLiquidated"))
            if name != "PositionDecreased" or end_lot == 0:
                if r.get("leverageHdths"):
                    cur["leverage"] = int(r["leverageHdths"]) / 100.0
                trips.append(rt._finish(cur, t_ms))
                open_.pop(key, None)
        elif name == "PositionInverted":
            if cur is not None:
                qty = max(cur["entry_qty"] - cur["exit_qty"], 0.0)
                close_part(cur, qty, px, pnl, fund, fee_usd * 0.5)
                trips.append(rt._finish(cur, t_ms))
            new_side = "short" if side == "long" else "long"
            t = new_trip(new_side, end_lot / (10 ** sd), px)
            t["fees"] = fee_usd * 0.5
            if r.get("leverageHdths"):
                t["leverage"] = int(r["leverageHdths"]) / 100.0
            open_[key] = t
    for t in trips:
        rt.apply_leverage(t, t.get("leverage"))
    trips.sort(key=lambda t: (t["close_time"], t["open_time"]))
    return {"trips": trips, "complete": complete, "first_event_at": first_at, "n_events": len(rows)}


async def run_perpl(coins: set[str]) -> dict:
    """Daily Perpl pass: leaderboard wallets with indexed accounts. Writes
    round_trips (venue perpl) and featured_wallets rows. DB only."""
    from app.services.featured import jobs
    lo, hi = await index_range()
    if hi is None:
        return {"wallets": 0, "note": "perpl_events empty"}
    dec = await _decimals()
    sf = get_session_factory()
    async with sf() as s:
        wallets = (await s.execute(text(
            "SELECT DISTINCT LOWER(s.wallet_address) FROM leaderboard_snapshots s JOIN ("
            " SELECT period, MAX(timestamp) mt FROM leaderboard_snapshots WHERE exchange='perpl' GROUP BY period"
            ") lb ON lb.period=s.period AND lb.mt=s.timestamp JOIN perpl_addr_map m ON m.address = LOWER(s.wallet_address) "
            "WHERE s.exchange='perpl' ORDER BY 1 LIMIT :n"), {"n": MAX_WALLETS})).scalars().all()
    now_ms = int(time.time() * 1000)
    index_stale = (datetime.utcnow() - hi).total_seconds() > 86400
    done = 0
    for w in wallets:
        try:
            b = await build_for_address(w, dec)
            if not b["trips"]:
                continue
            await jobs.write_trips("perpl", w, b["trips"])
            m = rt.compute_metrics(b["trips"], now_ms=now_ms, equity_avg=None, account_value=None, perpl_coins=coins,
                                   last_fill_ms=b["trips"][-1]["close_time"], identity_role=None, hedger=None,
                                   wash_share=None, mm_flag=None)
            m["flags"]["history"] = {
                "pass": bool(b["complete"]) and not index_stale,
                "value": (f"indexed {lo.date().isoformat()} to {hi.date().isoformat()}"
                          + ("" if b["complete"] else ", first trade before the index")),
            }
            if not m["flags"]["history"]["pass"]:
                m["qualified"] = False
            m["flags"]["venue_note"] = {"pass": None, "value": "single-venue view"}
            await jobs.write_wallet_row("perpl", w, m, None)
            done += 1
        except Exception as exc:
            logger.warning("featured perpl: %s failed: %s", w[:10], exc)
    logger.info("featured perpl: %d wallets written (index %s to %s)", done, lo, hi)
    return {"wallets": done, "index_from": lo.isoformat() if lo else None, "index_to": hi.isoformat()}
