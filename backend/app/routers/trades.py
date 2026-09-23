"""Trade history: save every fill, fetch history, PnL card data."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, desc, func

from app.db.database import get_session_factory
from app.db.models import User, TradeHistory, OrderHistory, CopyPosition
from app.routers.auth import get_authenticated_user
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/trades", tags=["trades"])

def _symbol_for(market_id: int) -> str:
    from app.services.chain_reader import market_symbol
    return market_symbol(market_id)


class SaveTradeRequest(BaseModel):
    market_id: int
    symbol: str
    side: str         # long/short
    action: str       # open/close
    order_type: str   # market/limit
    size: float
    price: float
    leverage: float | None = None
    fee: float | None = None
    notional: float | None = None
    pnl: float | None = None
    order_id: int | None = None
    raw_response: dict | None = None
    source: str = "manual"


@router.post("")
async def save_trade(
    req: SaveTradeRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Save a trade fill to history."""
    sf = get_session_factory()
    async with sf() as session:
        trade = TradeHistory(
            user_id=user.id,
            wallet_address=user.wallet_address.lower(),
            market_id=req.market_id,
            symbol=req.symbol or _symbol_for(req.market_id),
            side=req.side,
            action=req.action,
            order_type=req.order_type,
            size=req.size,
            price=req.price,
            leverage=req.leverage,
            fee=req.fee,
            notional=req.notional,
            pnl=req.pnl,
            order_id=req.order_id,
            raw_response=req.raw_response,
            source=req.source,
            created_at=datetime.now(timezone.utc),
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)

    return {"id": trade.id, "saved": True}


@router.get("/history")
async def get_trade_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    market_id: int | None = None,
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Fetch trade history for the authenticated user."""
    sf = get_session_factory()
    async with sf() as session:
        stmt = select(TradeHistory).where(TradeHistory.user_id == user.id)
        if market_id is not None:
            stmt = stmt.where(TradeHistory.market_id == market_id)
        stmt = stmt.order_by(desc(TradeHistory.created_at)).offset(skip).limit(limit)
        result = await session.execute(stmt)
        trades = result.scalars().all()

    return [
        {
            "id": t.id,
            "time": t.created_at.isoformat() if t.created_at else None,
            "market_id": t.market_id,
            "symbol": t.symbol,
            "side": t.side,
            "action": t.action,
            "order_type": t.order_type,
            "size": t.size,
            "price": t.price,
            "leverage": t.leverage,
            "fee": t.fee,
            "notional": t.notional,
            "pnl": t.pnl,
            "order_id": t.order_id,
            "source": t.source,
        }
        for t in trades
    ]


@router.get("/pnl-card")
async def get_pnl_card(
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Aggregate PnL stats for card generation."""
    sf = get_session_factory()
    async with sf() as session:
        # All trades
        result = await session.execute(
            select(TradeHistory).where(TradeHistory.user_id == user.id)
            .order_by(desc(TradeHistory.created_at))
            .limit(1000)
        )
        trades = result.scalars().all()

    if not trades:
        return {"total_trades": 0}

    total_pnl = sum(t.pnl or 0 for t in trades if t.action == "close")
    total_volume = sum((t.notional or t.size * t.price) for t in trades)
    total_fees = sum(t.fee or 0 for t in trades)
    wins = sum(1 for t in trades if t.action == "close" and (t.pnl or 0) > 0)
    losses = sum(1 for t in trades if t.action == "close" and (t.pnl or 0) < 0)
    close_count = wins + losses

    # Per-market breakdown
    by_market: dict[str, dict] = {}
    for t in trades:
        sym = t.symbol
        if sym not in by_market:
            by_market[sym] = {"trades": 0, "pnl": 0, "volume": 0}
        by_market[sym]["trades"] += 1
        by_market[sym]["volume"] += t.notional or (t.size * t.price)
        if t.action == "close":
            by_market[sym]["pnl"] += t.pnl or 0

    # Best and worst trade
    close_trades = [t for t in trades if t.action == "close" and t.pnl is not None]
    best = max(close_trades, key=lambda t: t.pnl, default=None)
    worst = min(close_trades, key=lambda t: t.pnl, default=None)

    # Today's trades
    from datetime import timedelta
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_trades = [t for t in trades if t.created_at and t.created_at >= today_start]
    today_pnl = sum(t.pnl or 0 for t in today_trades if t.action == "close")

    return {
        "wallet_address": trades[0].wallet_address if trades else "",
        "total_trades": len(trades),
        "today_trades": len(today_trades),
        "today_pnl": round(today_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "total_volume": round(total_volume, 2),
        "total_fees": round(total_fees, 2),
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / close_count * 100) if close_count > 0 else 0, 1),
        "best_trade": {
            "symbol": best.symbol, "side": best.side, "pnl": round(best.pnl, 2),
            "price": best.price, "size": best.size,
        } if best else None,
        "worst_trade": {
            "symbol": worst.symbol, "side": worst.side, "pnl": round(worst.pnl, 2),
            "price": worst.price, "size": worst.size,
        } if worst else None,
        "by_market": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in by_market.items()},
        "first_trade": trades[-1].created_at.isoformat() if trades else None,
        "last_trade": trades[0].created_at.isoformat() if trades else None,
    }


@router.get("/copy-performance")
async def get_copy_performance(
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Per-leader copy performance from copy_positions table."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyPosition).where(
                CopyPosition.follower_wallet == user.wallet_address.lower()
            ).order_by(desc(CopyPosition.opened_at))
        )
        copies = result.scalars().all()

    if not copies:
        return []

    # Group by leader
    by_leader: dict[str, dict] = {}
    for cp in copies:
        lw = cp.leader_wallet
        if lw not in by_leader:
            by_leader[lw] = {"leader_wallet": lw, "total": 0, "closed": 0, "open": 0, "pnl": 0, "volume": 0, "wins": 0, "losses": 0, "trades": []}
        by_leader[lw]["total"] += 1
        by_leader[lw]["volume"] += cp.allocation_usd or 0
        if cp.status == "closed":
            by_leader[lw]["closed"] += 1
            rpnl = cp.realized_pnl or 0
            by_leader[lw]["pnl"] += rpnl
            if rpnl > 0:
                by_leader[lw]["wins"] += 1
            elif rpnl < 0:
                by_leader[lw]["losses"] += 1
            by_leader[lw]["trades"].append({
                "symbol": cp.symbol, "side": cp.side,
                "entry_price": cp.entry_price, "close_price": cp.close_price,
                "size": cp.size, "pnl": round(rpnl, 2),
                "opened_at": cp.opened_at.isoformat() if cp.opened_at else None,
                "closed_at": cp.closed_at.isoformat() if cp.closed_at else None,
            })
        else:
            by_leader[lw]["open"] += 1

    leaders = []
    for lw, stats in by_leader.items():
        closed = stats["closed"]
        leaders.append({
            "leader_wallet": lw,
            "total_copies": stats["total"],
            "open_copies": stats["open"],
            "closed_copies": closed,
            "total_pnl": round(stats["pnl"], 2),
            "total_volume": round(stats["volume"], 2),
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": round(stats["wins"] / closed * 100, 1) if closed > 0 else 0,
            "trades": stats["trades"][:10],  # last 10 closed trades
        })

    # Sort by total PnL descending
    leaders.sort(key=lambda x: x["total_pnl"], reverse=True)
    return leaders


# ---- Order History ----

class SaveOrderRequest(BaseModel):
    market_id: int
    symbol: str
    direction: str        # Open Long, Close Short, etc.
    order_type: str       # market / limit
    size: float
    filled_size: float | None = None
    order_value: float | None = None
    price: float | None = None
    fill_price: float | None = None
    reduce_only: bool = False
    status: str           # filled / failed / cancelled / open / expired
    order_id: str | None = None
    fee: float | None = None
    pnl: float | None = None
    source: str = "manual"
    error: str | None = None
    raw_response: dict | None = None


@router.post("/orders")
async def save_order(
    req: SaveOrderRequest,
    user: User = Depends(get_authenticated_user),
) -> dict:
    """Save an order attempt to history."""
    sf = get_session_factory()
    async with sf() as session:
        order = OrderHistory(
            user_id=user.id,
            wallet_address=user.wallet_address.lower(),
            market_id=req.market_id,
            symbol=req.symbol or _symbol_for(req.market_id),
            direction=req.direction,
            order_type=req.order_type,
            size=req.size,
            filled_size=req.filled_size,
            order_value=req.order_value,
            price=req.price,
            fill_price=req.fill_price,
            reduce_only=req.reduce_only,
            status=req.status,
            order_id=req.order_id,
            fee=req.fee,
            pnl=req.pnl,
            source=req.source,
            error=req.error,
            raw_response=req.raw_response,
            created_at=datetime.now(timezone.utc),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

    return {"id": order.id, "saved": True}


@router.get("/orders")
async def get_order_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    market_id: int | None = None,
    user: User = Depends(get_authenticated_user),
) -> list[dict]:
    """Fetch order history for the authenticated user."""
    sf = get_session_factory()
    async with sf() as session:
        stmt = select(OrderHistory).where(OrderHistory.user_id == user.id)
        if market_id is not None:
            stmt = stmt.where(OrderHistory.market_id == market_id)
        stmt = stmt.order_by(desc(OrderHistory.created_at)).offset(skip).limit(limit)
        result = await session.execute(stmt)
        orders = result.scalars().all()

    return [
        {
            "id": o.id,
            "time": o.created_at.isoformat() if o.created_at else None,
            "market_id": o.market_id,
            "symbol": o.symbol,
            "direction": o.direction,
            "order_type": o.order_type,
            "size": o.size,
            "filled_size": o.filled_size,
            "order_value": o.order_value,
            "price": o.price,
            "fill_price": o.fill_price,
            "reduce_only": o.reduce_only,
            "status": o.status,
            "order_id": o.order_id,
            "fee": o.fee,
            "pnl": o.pnl,
            "source": o.source,
            "error": o.error,
        }
        for o in orders
    ]
