"""Account tools — require an authenticated MCP token.

The wallet is always resolved from the token; tools never accept a wallet
parameter for "read your own data" operations. This prevents one user from
accessing another user's data even if they know the wallet address.
"""
import asyncio
from typing import Optional

from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.models import (
    EquitySnapshot,
    PriceAlertDB,
    StopOrder,
    TradeJournal,
    CopyTradeLog,
)
from app.mcp.auth import current_user
from app.mcp.server import server
from app.utils.logger import get_logger

logger = get_logger(__name__)


@server.tool()
async def get_my_positions() -> dict:
    """Get the authenticated user's live on-chain positions on Perpl.
    Returns balance, margin used, all open positions with PnL, leverage,
    and any pending limit orders.

    Requires the user to have a Perpl account on-chain (made a deposit).
    """
    from app.services.chain_reader import get_trader_detail

    user = await current_user()
    detail = await asyncio.get_event_loop().run_in_executor(
        None, get_trader_detail, user.wallet_address
    )
    if not detail:
        return {
            "connected": False,
            "wallet": user.wallet_address,
            "message": "No Perpl account found on-chain. Deposit at perpl.xyz to get started.",
        }
    detail["connected"] = True
    return detail


@server.tool()
async def get_account_health() -> dict:
    """Full account health dashboard: equity, margin ratio, account leverage,
    per-position health scores, liquidation distances, and SL/TP coverage.
    Use this to assess risk before suggesting any trade.
    """
    from app.routers.health_dashboard import get_account_health as _impl
    user = await current_user()
    # _impl is a FastAPI endpoint that takes a User dep — call it as a plain function
    return await _impl(user=user)


@server.tool()
async def get_equity_curve(days: int = 30) -> list[dict]:
    """Historical equity snapshots over the past `days` (default 30, max 365).
    Useful for understanding the user's recent performance trajectory.
    """
    from datetime import datetime, timedelta

    user = await current_user()
    days = max(1, min(days, 365))
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
            "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            "equity": s.equity,
            "balance": s.balance,
            "unrealized_pnl": s.unrealized_pnl,
            "margin_used": s.margin_used,
            "position_count": s.position_count,
        }
        for s in snapshots
    ]


@server.tool()
async def get_my_sl_tp(status: str = "active") -> list[dict]:
    """List the user's stop-loss / take-profit orders monitored by the server.
    `status` can be 'active', 'triggered', 'cancelled', or 'all'.
    """
    from app.services.sl_tp_service import get_user_orders

    user = await current_user()
    return await get_user_orders(user.id, status)


@server.tool()
async def get_my_alerts() -> list[dict]:
    """List the user's active price alerts."""
    user = await current_user()
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(PriceAlertDB)
            .where(PriceAlertDB.user_id == user.id)
            .order_by(desc(PriceAlertDB.created_at))
        )
        rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "market_id": r.market_id,
            "symbol": r.symbol,
            "condition": r.condition,
            "target_price": r.target_price,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "triggered_at": r.triggered_at.isoformat() if r.triggered_at else None,
        }
        for r in rows
    ]


@server.tool()
async def get_my_orders(limit: int = 50) -> list[dict]:
    """Recent order history (copied + manual). Returns up to `limit` rows
    sorted newest first.
    """
    user = await current_user()
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyTradeLog)
            .where(CopyTradeLog.user_wallet == user.wallet_address.lower())
            .order_by(desc(CopyTradeLog.timestamp))
            .limit(limit)
        )
        rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "leader_wallet": r.leader_wallet,
            "market_id": r.market_id,
            "symbol": r.symbol,
            "side": r.side,
            "leverage": r.leverage,
            "amount_usd": r.amount_usd,
            "status": r.status,
            "error": r.error,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        }
        for r in rows
    ]


@server.tool()
async def get_journal(market: Optional[str] = None, tag: Optional[str] = None, limit: int = 20) -> list[dict]:
    """List the user's trade-journal entries. Filter by market symbol or tag.
    Useful for reviewing the user's trading thesis history.
    """
    from app.mcp.tools.markets import _resolve_market_id

    user = await current_user()
    mid = _resolve_market_id(market) if market else None

    sf = get_session_factory()
    async with sf() as session:
        q = (
            select(TradeJournal)
            .where(TradeJournal.user_id == user.id)
            .order_by(desc(TradeJournal.created_at))
            .limit(limit)
        )
        if mid is not None:
            q = q.where(TradeJournal.market_id == mid)
        result = await session.execute(q)
        entries = result.scalars().all()

    out: list[dict] = []
    for e in entries:
        if tag and (not e.tags or tag not in e.tags):
            continue
        out.append({
            "id": e.id,
            "market_id": e.market_id,
            "symbol": e.symbol,
            "side": e.side,
            "entry_price": e.entry_price,
            "exit_price": e.exit_price,
            "pnl": e.pnl,
            "notes": e.notes,
            "tags": e.tags or [],
            "rating": e.rating,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return out


@server.tool()
async def add_journal_entry(
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: Optional[float] = None,
    pnl: Optional[float] = None,
    notes: Optional[str] = None,
    tags: Optional[list[str]] = None,
    rating: Optional[int] = None,
) -> dict:
    """Add a trade-journal entry. The only mutating tool in this surface;
    journal entries are scoped to the user and contain only text data.
    """
    from datetime import datetime
    from app.mcp.tools.markets import _resolve_market_id, _symbol_of

    user = await current_user(required_scope="journal")
    mid = await _resolve_market_id(symbol)
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    if rating is not None and not (1 <= rating <= 5):
        raise ValueError("rating must be 1-5")
    # Length caps to prevent storage abuse via leaked tokens.
    if notes is not None:
        notes = str(notes)[:2000]
    if tags is not None:
        if not isinstance(tags, list) or len(tags) > 20:
            raise ValueError("tags must be a list of <= 20 items")
        tags = [str(t)[:32] for t in tags]

    sf = get_session_factory()
    from datetime import timedelta
    from sqlalchemy import func
    day_ago = datetime.utcnow() - timedelta(hours=24)
    async with sf() as session:
        count_result = await session.execute(
            select(func.count(TradeJournal.id)).where(
                TradeJournal.user_id == user.id,
                TradeJournal.created_at >= day_ago,
            )
        )
        recent = count_result.scalar() or 0
        if recent >= 200:
            raise PermissionError(
                "Journal write quota exceeded (200/24h). Try again later."
            )

        entry = TradeJournal(
            user_id=user.id,
            market_id=mid,
            symbol=await _symbol_of(mid),
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            notes=notes,
            tags=tags,
            rating=rating,
            created_at=datetime.utcnow(),
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

    return {"id": entry.id, "created_at": entry.created_at.isoformat()}
