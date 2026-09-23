import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc

from app.db.database import get_session_factory
from app.db.models import User, StopOrder, EquitySnapshot
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/account-health", tags=["health"])


@router.get("")
async def get_account_health(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Full account health dashboard data — positions, risk metrics, SL/TP coverage."""
    from app.services.chain_reader import get_trader_detail, MARKETS
    from app.services.ws_manager import ws_manager

    # Get on-chain data
    detail = await asyncio.get_event_loop().run_in_executor(
        None, get_trader_detail, user.wallet_address
    )
    if not detail:
        return {
            "connected": False,
            "message": "No Perpl account found on-chain",
        }

    balance = detail["balance"]
    margin_used = detail["margin_used"]
    positions = detail["positions"]

    # Get active SL/TP counts per market
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(StopOrder.market_id, StopOrder.order_type, func.count(StopOrder.id))
            .where(StopOrder.user_id == user.id, StopOrder.status == "active")
            .group_by(StopOrder.market_id, StopOrder.order_type)
        )
        sl_tp_counts: dict[int, dict[str, int]] = {}
        for market_id, order_type, count in result.all():
            if market_id not in sl_tp_counts:
                sl_tp_counts[market_id] = {"sl": 0, "tp": 0}
            sl_tp_counts[market_id][order_type] = count

    # Compute per-position health
    total_unrealized_pnl = 0
    total_notional = 0
    position_health = []

    for pos in positions:
        market_id = pos["market_id"]
        mcfg = MARKETS.get(market_id, {})

        # Use live mark price from ws_manager cache if available
        live_mark = pos["mark_price"]
        if ws_manager and market_id in ws_manager.market_state_cache:
            live_mark = ws_manager.market_state_cache[market_id].get("mark_price", live_mark)

        size = pos["size"]
        entry = pos["entry_price"]
        deposit = pos["deposit"]
        side = pos["side"]
        notional = size * live_mark

        # PnL
        pnl = (live_mark - entry) * size if side == "long" else (entry - live_mark) * size
        funding_pnl = pos.get("funding_pnl", 0)
        total_pnl = pnl + funding_pnl
        roe = (total_pnl / deposit * 100) if deposit > 0 else 0

        # Liquidation
        mmr_hdths = 2000  # default
        from app.services.ws_manager import ws_manager as _wm
        if _wm and market_id in _wm._market_configs:
            mmr_hdths = _wm._market_configs[market_id].get("maintenance_margin", 2000)
        mmr_fraction = 100 / mmr_hdths
        mmr_usd = notional * mmr_fraction

        if size > 0:
            liq_price = (entry - (deposit - mmr_usd) / size) if side == "long" else (entry + (deposit - mmr_usd) / size)
        else:
            liq_price = 0

        liq_distance_pct = abs(liq_price - live_mark) / live_mark * 100 if live_mark > 0 else 999

        # Health score: 0-100 (0 = about to liquidate, 100 = very safe)
        health_score = min(100, max(0, liq_distance_pct * 3))

        # SL/TP coverage
        sl_tp = sl_tp_counts.get(market_id, {"sl": 0, "tp": 0})

        total_unrealized_pnl += total_pnl
        total_notional += notional

        position_health.append({
            "market_id": market_id,
            "symbol": pos["symbol"],
            "side": side,
            "size": size,
            "entry_price": entry,
            "mark_price": live_mark,
            "leverage": pos["leverage"],
            "deposit": deposit,
            "notional": round(notional, 2),
            "pnl": round(pnl, 2),
            "funding_pnl": round(funding_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "roe_pct": round(roe, 2),
            "liq_price": round(max(0, liq_price), mcfg.get("price_decimals", 2)),
            "liq_distance_pct": round(liq_distance_pct, 2),
            "health_score": round(health_score),
            "has_sl": sl_tp["sl"] > 0,
            "has_tp": sl_tp["tp"] > 0,
            "mmr_usd": round(mmr_usd, 2),
        })

    # Account-level metrics
    equity = balance + margin_used + total_unrealized_pnl
    margin_ratio = (margin_used / equity * 100) if equity > 0 else 0
    available_balance = balance + total_unrealized_pnl
    account_leverage = (total_notional / equity) if equity > 0 else 0

    # Closest liquidation
    closest_liq = min(position_health, key=lambda p: p["liq_distance_pct"]) if position_health else None

    # Overall risk level
    if not closest_liq:
        risk_level = "safe"
    elif closest_liq["liq_distance_pct"] < 5:
        risk_level = "critical"
    elif closest_liq["liq_distance_pct"] < 10:
        risk_level = "danger"
    elif closest_liq["liq_distance_pct"] < 20:
        risk_level = "warning"
    else:
        risk_level = "safe"

    # SL/TP coverage summary
    positions_with_sl = sum(1 for p in position_health if p["has_sl"])
    positions_with_tp = sum(1 for p in position_health if p["has_tp"])

    return {
        "connected": True,
        "wallet_address": user.wallet_address,
        "account": {
            "equity": round(equity, 2),
            "balance": round(balance, 2),
            "available": round(available_balance, 2),
            "margin_used": round(margin_used, 2),
            "margin_ratio_pct": round(margin_ratio, 2),
            "account_leverage": round(account_leverage, 2),
            "total_unrealized_pnl": round(total_unrealized_pnl, 2),
            "total_notional": round(total_notional, 2),
            "position_count": len(positions),
        },
        "risk": {
            "level": risk_level,
            "closest_liq_pct": round(closest_liq["liq_distance_pct"], 2) if closest_liq else None,
            "closest_liq_market": closest_liq["symbol"] if closest_liq else None,
            "positions_with_sl": positions_with_sl,
            "positions_with_tp": positions_with_tp,
            "unprotected_positions": len(position_health) - positions_with_sl,
        },
        "positions": position_health,
    }


@router.get("/equity-curve")
async def get_equity_curve(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Get equity snapshots for the equity curve chart."""
    from datetime import datetime, timedelta

    since = datetime.utcnow() - timedelta(days=days)
    wallet = user.wallet_address.lower()

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(EquitySnapshot)
            .where(
                EquitySnapshot.wallet_address == wallet,
                EquitySnapshot.timestamp >= since,
            )
            .order_by(EquitySnapshot.timestamp)
        )
        snapshots = result.scalars().all()

    return [
        {
            "timestamp": s.timestamp.isoformat(),
            "equity": s.equity,
            "balance": s.balance,
            "unrealized_pnl": s.unrealized_pnl,
            "margin_used": s.margin_used,
            "position_count": s.position_count,
        }
        for s in snapshots
    ]


@router.post("/equity-curve/snapshot")
async def force_equity_snapshot(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Force an immediate equity snapshot for the current user (for initial data)."""
    from app.services.chain_reader import get_trader_detail
    from app.services.ws_manager import ws_manager as _wm
    from datetime import datetime

    detail = await asyncio.get_event_loop().run_in_executor(
        None, get_trader_detail, user.wallet_address
    )
    if not detail:
        raise HTTPException(status_code=404, detail="No on-chain account found")

    balance = detail["balance"]
    margin_used = detail["margin_used"]
    positions = detail["positions"]

    unrealized_pnl = 0
    for pos in positions:
        mid = pos["market_id"]
        mark = pos["mark_price"]
        if _wm and mid in _wm.market_state_cache:
            mark = _wm.market_state_cache[mid].get("mark_price", mark)
        pnl = (mark - pos["entry_price"]) * pos["size"] if pos["side"] == "long" \
            else (pos["entry_price"] - mark) * pos["size"]
        unrealized_pnl += pnl + pos.get("funding_pnl", 0)

    equity = balance + margin_used + unrealized_pnl

    sf = get_session_factory()
    async with sf() as session:
        snap = EquitySnapshot(
            wallet_address=user.wallet_address.lower(),
            equity=round(equity, 2),
            balance=round(balance, 2),
            unrealized_pnl=round(unrealized_pnl, 2),
            margin_used=round(margin_used, 2),
            position_count=len(positions),
            timestamp=datetime.utcnow(),
        )
        session.add(snap)
        await session.commit()

    return {"success": True, "equity": round(equity, 2)}
