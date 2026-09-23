"""Funding rate comparison across exchanges — Perpl vs Hyperliquid."""
from fastapi import APIRouter, Query

from app.services.hyperliquid_client import get_perpl_markets_funding, get_funding_history, get_all_funding_rates
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/funding-compare", tags=["funding"])


@router.get("")
async def get_funding_comparison() -> list[dict]:
    """Compare current funding rates: Perpl vs Hyperliquid for all markets."""
    # Perpl funding from cached market state (import at request time, not module level)
    from app.services.ws_manager import ws_manager as _wm
    perpl_rates = {}
    if _wm:
        for mid, state in _wm.market_state_cache.items():
            symbol = state.get("symbol", "")
            perpl_rates[symbol] = {
                "funding_rate": state.get("funding_rate", 0),
                "mark_price": state.get("mark_price", 0),
            }

    # Hyperliquid funding
    hl_rates = await get_perpl_markets_funding()

    # Build comparison — every live Perpl market (dynamic; HYPE/ZEC included),
    # ordered by market id so the table is stable.
    symbols = [s.get("symbol", "") for _, s in sorted(_wm.market_state_cache.items())] if _wm else []
    symbols = [s for s in symbols if s] or ["BTC", "ETH", "SOL", "MON"]
    result = []
    for symbol in symbols:
        perpl = perpl_rates.get(symbol, {})
        hl = hl_rates.get(symbol)

        perpl_fr = perpl.get("funding_rate", 0) or 0
        hl_fr = hl["funding_rate"] if hl else None

        # Signal: when both agree on extreme direction.
        # Thresholds are on the averaged decimal rate (e.g. 0.001 = 0.1%).
        # Check strongest condition first so weaker elif doesn't shadow it.
        signal = "neutral"
        if hl_fr is not None:
            avg = (perpl_fr + hl_fr) / 2
            if avg > 0.001:
                signal = "strong_short"
            elif avg > 0.0005:
                signal = "short_bias"
            elif avg < -0.0008:
                signal = "strong_long"
            elif avg < -0.0003:
                signal = "long_bias"

        entry = {
            "symbol": symbol,
            "perpl": {
                "funding_rate": perpl_fr,
                "funding_rate_pct": round(perpl_fr * 100, 4) if perpl_fr else 0,
                "mark_price": perpl.get("mark_price", 0),
            },
            "hyperliquid": {
                "funding_rate": hl_fr,
                "funding_rate_pct": round(hl_fr * 100, 4) if hl_fr else None,
                "mark_price": hl.get("mark_price", 0) if hl else None,
                "open_interest": hl.get("open_interest", 0) if hl else None,
                "volume_24h": hl.get("volume_24h", 0) if hl else None,
            } if hl else None,
            "signal": signal,
        }
        result.append(entry)

    return result


@router.get("/history/{symbol}")
async def get_funding_history_comparison(
    symbol: str,
    hours: int = Query(72, ge=1, le=720),
) -> dict:
    """Get historical funding rates for a symbol from Hyperliquid."""
    hl_history = await get_funding_history(symbol.upper(), hours)

    # Also get Perpl funding history from DB
    perpl_history = []
    try:
        from sqlalchemy import select
        from app.db.database import get_session_factory
        from app.db.models import FundingHistory
        from datetime import datetime, timedelta

        since = datetime.utcnow() - timedelta(hours=hours)
        # Map symbol to market_id
        sym_to_mid = {"BTC": 1, "ETH": 20, "SOL": 30, "MON": 10}
        mid = sym_to_mid.get(symbol.upper())

        if mid:
            sf = get_session_factory()
            async with sf() as session:
                result = await session.execute(
                    select(FundingHistory)
                    .where(FundingHistory.market_id == mid, FundingHistory.timestamp >= since)
                    .order_by(FundingHistory.timestamp)
                )
                rows = result.scalars().all()
                perpl_history = [
                    {
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                        "funding_rate": r.funding_rate,
                        "mark_price": r.mark_price,
                    }
                    for r in rows
                ]
    except Exception:
        logger.exception("Failed to get Perpl funding history")

    return {
        "symbol": symbol.upper(),
        "hyperliquid": hl_history,
        "perpl": perpl_history,
    }


@router.get("/hyperliquid/all")
async def get_all_hl_rates() -> dict:
    """Get all Hyperliquid funding rates (for exploration)."""
    rates = await get_all_funding_rates()
    return {"markets": len(rates), "data": rates}


@router.get("/orderbook/{symbol}")
async def get_hl_orderbook(symbol: str) -> dict:
    """Get Hyperliquid order book for a symbol — bids, asks, whale levels."""
    from app.services.hyperliquid_client import get_order_book
    return await get_order_book(symbol.upper())
