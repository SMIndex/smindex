"""Wallet Insights — win-rate ranked wallets (reconstructed from tracked
on-chain events) + per-wallet trade drill-down. Read-only.

Data notes surfaced to the UI:
  * only wallets the tracker has watched have data;
  * est_pnl is candle-priced and gross of fees (win = est_pnl > 0);
  * leverage was never recorded historically -> not returned per trade.
"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.database import get_session_factory
from app.services import wallet_insights
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/wallets")
async def list_wallets(min_trades: int = Query(1, ge=1, le=1000)) -> dict:
    """Wallets ordered by win rate (desc), then trade count. Win = est_pnl > 0;
    trades with no candle data (est_pnl NULL) are excluded from the rate."""
    sf = get_session_factory()
    async with sf() as session:
        res = await session.execute(text(
            "SELECT wallet, "
            "  COUNT(*) AS spans, "
            "  SUM(est_pnl IS NOT NULL) AS rated, "
            "  SUM(est_pnl > 0) AS wins, "
            "  SUM(COALESCE(est_pnl, 0)) AS est_pnl_total, "
            "  AVG(hold_sec) AS avg_hold_sec, "
            "  MIN(close_t) AS first_close, MAX(close_t) AS last_close "
            "FROM wallet_trade_insights GROUP BY wallet"
        ))
        rows = res.fetchall()
        mk = await session.execute(text(
            "SELECT wallet, symbol, COUNT(*) FROM wallet_trade_insights "
            "GROUP BY wallet, symbol"
        ))
        markets: dict[str, list] = {}
        for w, sym, n in mk.fetchall():
            markets.setdefault(w, []).append((sym, n))

    out = []
    for w, spans, rated, wins, pnl, avg_hold, first_c, last_c in rows:
        rated = int(rated or 0)
        if rated < min_trades:
            continue
        wins = int(wins or 0)
        out.append({
            "wallet": w,
            "trades": rated,
            "wins": wins,
            "losses": rated - wins,
            "win_rate": round(wins / rated * 100, 1) if rated else None,
            "est_pnl_total": round(float(pnl or 0), 2),
            "avg_hold_sec": int(avg_hold) if avg_hold is not None else None,
            "markets": [s for s, _ in sorted(markets.get(w, []), key=lambda x: -x[1])],
            "first_close": first_c.isoformat() + "Z" if first_c else None,
            "last_close": last_c.isoformat() + "Z" if last_c else None,
            "unrated_trades": int(spans) - rated,
        })
    out.sort(key=lambda r: (-(r["win_rate"] or 0), -r["trades"]))
    return {"wallets": out, "status": wallet_insights.status()}


@router.get("/wallets/{wallet}/trades")
async def wallet_trades(wallet: str, limit: int = Query(500, ge=1, le=2000)) -> dict:
    w = wallet.lower()
    if not (w.startswith("0x") and len(w) == 42):
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    sf = get_session_factory()
    async with sf() as session:
        res = await session.execute(text(
            "SELECT symbol, side, open_t, close_t, hold_sec, max_size, avg_entry, "
            "  notional_usd, leverage, est_pnl, legs "
            "FROM wallet_trade_insights WHERE wallet = :w "
            "ORDER BY close_t DESC LIMIT :lim"
        ), {"w": w, "lim": limit})
        rows = res.fetchall()
    trades = [{
        "symbol": sym, "side": side,
        "open_time": (ot.isoformat() + "Z") if ot else None,   # None = opened before tracking
        "close_time": ct.isoformat() + "Z",
        "hold_sec": hold,
        "size": float(ms) if ms is not None else None,
        "avg_entry": float(ae) if ae is not None else None,
        "notional_usd": round(float(no), 2) if no is not None else None,
        # leverage recorded from 2026-07-31 on (deposit-derived); older trades None
        "leverage": round(float(lev), 1) if lev is not None else None,
        "est_pnl": round(float(p), 2) if p is not None else None,
        "legs": legs,
    } for sym, side, ot, ct, hold, ms, ae, no, lev, p, legs in rows]
    return {"wallet": w, "trades": trades, "count": len(trades)}


@router.post("/refresh")
async def trigger_refresh() -> dict:
    st = wallet_insights.status()
    if st["running"]:
        return {"started": False, "status": st}
    import asyncio
    asyncio.create_task(wallet_insights.refresh())
    return {"started": True, "status": wallet_insights.status()}
