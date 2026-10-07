"""stage_order — propose a trade by returning a deep link the user clicks
to open the SMINDEX trade page with the order pre-filled.

This tool NEVER places an order. It validates the proposal against market
config (leverage cap, market existence, price required for limit), computes
estimated size / liquidation / fees / MMR, and returns an HMAC-signed envelope
embedded in a `?stage=` query param.

Critical: the envelope is signed (`stage_sig.sign_payload`) and bound to
`user_id` + a short expiry. The frontend POSTs the envelope to
`/api/mcp-stage/verify` (authenticated with the user's normal JWT) which
checks signature, expiry, and that the bound `uid` matches the current user.
Only then is the order dispatched into OrderForm. The deep link is
single-use, expires fast, and only fires for its intended user.
"""
from typing import Literal, Optional

from app.mcp.auth import current_user
from app.mcp.server import server
from app.mcp.stage_sig import sign_payload
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Legacy fallback only — Perpl's real caps are DYNAMIC (max = initial_margin/100,
# same rule as main.py._get_real_max_leverage; live 2026-08: BTC 15x, MON 10x,
# ETH/SOL 12x, HYPE 10x, ZEC 8x). This stale map (pre-2026-07 margins) is used
# only if the live config cache is empty.
REAL_MAX_LEVERAGE: dict[int, int] = {1: 10, 10: 5, 20: 10, 30: 20}


def _live_max_leverage(market_id: int) -> int:
    """Max leverage from the live market config cache (initial_margin/100)."""
    try:
        from app.services.ws_manager import ws_manager
        if ws_manager:
            init = ws_manager._market_configs.get(market_id, {}).get("initial_margin", 0)
            if init and init > 0:
                return max(1, round(init / 100))
    except Exception:
        pass
    return REAL_MAX_LEVERAGE.get(market_id, 10)

# Where the terminal frontend lives. Read from env so dev / prod differ.
import os
TERMINAL_BASE_URL = os.environ.get("PERPL_TERMINAL_URL", "https://smindex.xyz")


@server.tool()
async def stage_order(
    symbol: str,
    side: Literal["long", "short"],
    order_type: Literal["market", "limit"],
    amount_usd: float,
    leverage: int,
    price: Optional[float] = None,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    rationale: Optional[str] = None,
) -> dict:
    """Propose a trade for the user to review.

    This tool DOES NOT place an order. It returns a deep link that opens the
    SMINDEX trade page with all fields pre-filled. The user reviews
    everything in the UI, can change any field, and clicks Place to actually
    submit. The link is single-use; refreshing the page won't re-stage.

    Always set BOTH `sl_price` and `tp_price`. If you can't justify a stop
    based on chart structure, you shouldn't be staging the trade.

    Args:
        symbol: BTC, ETH, SOL, or MON
        side: 'long' or 'short'
        order_type: 'market' (fills immediately) or 'limit' (rests in book)
        amount_usd: margin to commit (the cost basis, NOT the notional)
        leverage: integer multiplier; will be capped at the market's max
        price: required for 'limit' orders; ignored for 'market'
        sl_price: stop-loss trigger price (highly recommended)
        tp_price: take-profit trigger price (highly recommended)
        rationale: one-line trade thesis the user will see in the UI

    Returns: deep_link, summary, estimated metrics, and warnings.
    """
    # Lazy import — avoids a circular import (stage → markets → server → tools/*)
    from app.mcp.tools.markets import _resolve_market_id, _symbol_of

    # Token must explicitly carry the 'stage' scope. We need the user record
    # so we can bind the signed envelope to their id below.
    user = await current_user(required_scope="stage")

    warnings: list[str] = []

    # ----- validate symbol (live registry — SOL=31, HYPE/ZEC included) ------
    try:
        market_id = await _resolve_market_id(symbol)
    except ValueError as exc:
        raise ValueError(str(exc))
    sym = await _symbol_of(market_id)

    # ----- validate side / order_type ---------------------------------------
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    if order_type not in ("market", "limit"):
        raise ValueError("order_type must be 'market' or 'limit'")
    if order_type == "limit" and (price is None or price <= 0):
        raise ValueError("price is required for limit orders")
    if amount_usd is None or amount_usd <= 0:
        raise ValueError("amount_usd must be > 0")

    # ----- leverage cap (dynamic from live initial_margin) ------------------
    max_lev = _live_max_leverage(market_id)
    if leverage <= 0:
        raise ValueError("leverage must be > 0")
    capped_leverage = leverage
    if leverage > max_lev:
        warnings.append(
            f"Requested leverage {leverage}x exceeds {sym} max {max_lev}x — capped to {max_lev}x."
        )
        capped_leverage = max_lev

    # ----- pull live market state for size / liq estimation -----------------
    from app.services.ws_manager import ws_manager
    state = ws_manager.market_state_cache.get(market_id, {}) if ws_manager else {}
    mark_price = state.get("mark_price") or 0
    bid_price = state.get("bid_price") or mark_price
    ask_price = state.get("ask_price") or mark_price
    if not mark_price:
        warnings.append("Live market state unavailable — estimates may be missing.")

    fill_price: float
    if order_type == "market":
        fill_price = ask_price if side == "long" else bid_price
    else:
        fill_price = float(price)  # type: ignore[arg-type]

    notional = amount_usd * capped_leverage
    size = (notional / fill_price) if fill_price else 0

    # ----- maintenance margin / liquidation ---------------------------------
    mmr_hdths = 2000  # default
    if ws_manager and market_id in ws_manager._market_configs:
        mmr_hdths = ws_manager._market_configs[market_id].get("maintenance_margin", 2000)
    mmr_fraction = 100 / mmr_hdths
    mmr_usd = notional * mmr_fraction

    if size > 0:
        if side == "long":
            liq_price = fill_price - (amount_usd - mmr_usd) / size
        else:
            liq_price = fill_price + (amount_usd - mmr_usd) / size
        liq_price = max(0, liq_price)
    else:
        liq_price = 0

    # ----- fee estimate (taker for market, maker for limit) -----------------
    # Raw context fee fields are hundredths of a bps (taker_fee=690 -> 6.9 bps);
    # raw/100 = bps, then bps/10000 = fraction.
    cfg = ws_manager._market_configs.get(market_id, {}) if ws_manager else {}
    taker_bps = cfg.get("taker_fee", 690) / 100
    maker_bps = cfg.get("maker_fee", 90) / 100
    fee_bps = taker_bps if order_type == "market" else maker_bps
    fee_usd = notional * (fee_bps / 10000)

    # ----- safety warnings --------------------------------------------------
    if sl_price is None:
        warnings.append("No stop-loss set — strongly recommended for any leveraged position.")
    else:
        if side == "long" and sl_price >= fill_price:
            warnings.append("SL price is at or above the entry on a long — order would trigger immediately.")
        if side == "short" and sl_price <= fill_price:
            warnings.append("SL price is at or below the entry on a short — order would trigger immediately.")
    if tp_price is None:
        warnings.append("No take-profit set.")
    if amount_usd < 1:
        warnings.append("amount_usd is below $1 — Perpl may reject very small orders.")
    if capped_leverage >= max_lev:
        warnings.append(f"Using max leverage ({max_lev}x) — high liquidation risk.")
    if liq_price > 0 and mark_price > 0:
        liq_distance_pct = abs(liq_price - mark_price) / mark_price * 100
        if liq_distance_pct < 5:
            warnings.append(
                f"Liquidation only {liq_distance_pct:.1f}% away from mark — extremely risky."
            )

    # ----- build payload + summary ------------------------------------------
    payload = {
        "v": 1,
        "marketId": market_id,
        "symbol": sym,
        "side": side,
        "mode": order_type,
        "amount": round(amount_usd, 4),
        "leverage": int(capped_leverage),
        "price": round(float(price), 8) if price is not None else None,
        "sl": round(float(sl_price), 8) if sl_price is not None else None,
        "tp": round(float(tp_price), 8) if tp_price is not None else None,
        "rationale": (rationale or "").strip()[:200] or None,
    }

    # Sign the payload + bind to user_id with a short TTL. The frontend will
    # POST this envelope to /api/mcp-stage/verify before dispatching.
    envelope = sign_payload(payload, user_id=user.id)
    deep_link = f"{TERMINAL_BASE_URL}/trade?stage={envelope}"
    logger.info(
        "MCP stage_order issued user_id=%s symbol=%s side=%s mode=%s",
        user.id, sym, side, order_type,
    )

    summary_parts = [
        f"{side.upper()} {round(size, 6)} {sym}",
        f"@ {order_type}" + (f" {price}" if order_type == "limit" else ""),
        f"{capped_leverage}x lev",
        f"${round(amount_usd, 2)} margin",
    ]
    if sl_price is not None:
        summary_parts.append(f"SL ${sl_price}")
    if tp_price is not None:
        summary_parts.append(f"TP ${tp_price}")
    summary = ", ".join(summary_parts)

    return {
        "deep_link": deep_link,
        "summary": summary,
        "estimated": {
            "fill_price": round(fill_price, 8) if fill_price else None,
            "size": round(size, 8) if size else None,
            "notional_usd": round(notional, 2),
            "fee_usd": round(fee_usd, 4),
            "liq_price": round(liq_price, 8) if liq_price else None,
            "liq_distance_pct": round(abs(liq_price - mark_price) / mark_price * 100, 2)
                if liq_price and mark_price else None,
            "mmr_usd": round(mmr_usd, 4),
            "max_leverage": max_lev,
            "applied_leverage": capped_leverage,
        },
        "warnings": warnings,
        "instructions": (
            "Click the deep_link to open SMINDEX with this order pre-filled. "
            "Review every field, then click Place to submit. Nothing has been sent yet."
        ),
    }
