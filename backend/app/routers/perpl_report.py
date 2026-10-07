"""Perpl Platform Report — aggregated stats from Perpl public API."""

from fastapi import APIRouter

from app.services.perpl_client import perpl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/perpl-report", tags=["perpl-report"])

# Decimals from the live chain_reader map (registry-synced), with a .get()-style
# shim so existing call sites keep working for HYPE/ZEC/SOL-31.
class _DecimalMap:
    def __init__(self, key):
        self._key = key

    def get(self, mid, default):
        from app.services.chain_reader import MARKETS
        m = MARKETS.get(mid)
        return m[self._key] if m else default


PRICE_DECIMALS = _DecimalMap("price_decimals")
SIZE_DECIMALS = _DecimalMap("size_decimals")


@router.get("")
async def get_perpl_report() -> dict:
    """Aggregate Perpl platform stats from public API. No auth required."""
    ctx = await perpl_client.get_context()
    markets = ctx.get("markets", [])

    market_stats = []
    total_oi_usd = 0
    total_volume_24h = 0
    total_tvl = 0

    for m in markets:
        mid = m.get("id", 0)
        name = m.get("name", "?")
        state = m.get("state", {})
        config = m.get("config", {})
        funding = m.get("funding", {})

        pd = 10 ** PRICE_DECIMALS.get(mid, 2)

        # Perpl API may return some values as strings — cast to int
        _int = lambda v: int(v) if v else 0

        mark_price = _int(state.get("mrk", 0)) / pd
        prev_price = _int(state.get("prv", 0)) / pd
        change_24h = ((mark_price - prev_price) / prev_price * 100) if prev_price else 0
        volume_24h_usd = _int(state.get("dva", 0)) / 1e6
        sd = 10 ** SIZE_DECIMALS.get(mid, 0)
        oi_raw = _int(state.get("oi", 0))
        oi_contracts = oi_raw / sd  # actual quantity in base units
        oi_usd = oi_contracts * mark_price
        tvl = _int(state.get("tvl", 0)) / 1e6
        bid = _int(state.get("bid", 0)) / pd
        ask = _int(state.get("ask", 0)) / pd
        spread = ask - bid if ask and bid else 0
        spread_pct = (spread / mark_price * 100) if mark_price else 0
        funding_rate = _int(funding.get("rate", 0)) / 10000
        funding_rate_pct = funding_rate * 100

        total_oi_usd += oi_usd
        total_volume_24h += volume_24h_usd
        total_tvl += tvl

        market_stats.append({
            "market_id": mid,
            "symbol": name,
            "mark_price": round(mark_price, PRICE_DECIMALS.get(mid, 2)),
            "change_24h_pct": round(change_24h, 2),
            "volume_24h_usd": round(volume_24h_usd, 2),
            "open_interest_qty": round(oi_contracts, 4),
            "open_interest_usd": round(oi_usd, 2),
            "tvl": round(tvl, 2),
            "bid": round(bid, PRICE_DECIMALS.get(mid, 2)),
            "ask": round(ask, PRICE_DECIMALS.get(mid, 2)),
            "spread": round(spread, PRICE_DECIMALS.get(mid, 2) + 2),
            "spread_pct": round(spread_pct, 4),
            "funding_rate": round(funding_rate, 8),
            "funding_rate_pct": round(funding_rate_pct, 4),
            "funding_direction": "longs pay" if funding_rate > 0 else "shorts pay" if funding_rate < 0 else "neutral",
        })

    # Leaderboard stats
    try:
        lb_all = await perpl_client.get_leaderboard_parsed("all", "pnl")
        lb_day = await perpl_client.get_leaderboard_parsed("day", "vol")
    except Exception:
        lb_all = []
        lb_day = []

    total_traders = len(lb_all)
    total_pnl = sum(t["pnl_total"] for t in lb_all)
    total_volume = sum(t["volume"] for t in lb_all)
    profitable_traders = sum(1 for t in lb_all if t["pnl_total"] > 0)
    losing_traders = sum(1 for t in lb_all if t["pnl_total"] < 0)

    active_24h = len([t for t in lb_day if t["volume"] > 0])
    volume_24h_lb = sum(t["volume"] for t in lb_day)

    top_by_pnl = sorted(lb_all, key=lambda t: t["pnl_total"], reverse=True)[:5]
    top_by_volume = sorted(lb_all, key=lambda t: t["volume"], reverse=True)[:5]

    return {
        "platform": {
            "total_tvl": round(total_tvl, 2),
            "total_open_interest_usd": round(total_oi_usd, 2),
            "total_volume_24h": round(total_volume_24h, 2),
        },
        "markets": market_stats,
        "leaderboard": {
            "total_traders": total_traders,
            "profitable_traders": profitable_traders,
            "losing_traders": losing_traders,
            "active_24h": active_24h,
            "total_pnl": round(total_pnl, 2),
            "total_volume_all_time": round(total_volume, 2),
            "volume_24h": round(volume_24h_lb, 2),
            "top_by_pnl": [
                {"rank": t["rank"], "wallet": t["wallet_address"], "pnl": round(t["pnl_total"], 2), "volume": round(t["volume"], 2)}
                for t in top_by_pnl
            ],
            "top_by_volume": [
                {"rank": t["rank"], "wallet": t["wallet_address"], "pnl": round(t["pnl_total"], 2), "volume": round(t["volume"], 2)}
                for t in top_by_volume
            ],
        },
    }
