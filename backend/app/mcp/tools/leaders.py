"""Leaderboard, follows, and copy-trade tools.

All tools require a valid MCP token. The "public-flavored" ones
(`get_leaderboard`, `get_leader_positions`) only need the `read` scope and
return data that is technically public on-chain — but token-gating them lets
us rate-limit per-token and prevent anonymous RPC abuse. The user-scoped
tools (`get_my_*`) require `read` and read by token-owner.
"""
import asyncio
from typing import Optional

from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.models import LeaderTrade, WalletFollow
from app.mcp.auth import current_user
from app.mcp.server import server
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ----- Public tools (no auth) ------------------------------------------------


@server.tool()
async def get_leaderboard(period: str = "all", sort: str = "pnl", limit: int = 20) -> list[dict]:
    """Top traders on Perpl by PnL or volume.

    `period`: 'all' (all-time) or 'day' (24h)
    `sort`: 'pnl' or 'vol'
    `limit`: how many to return (max 100)
    """
    from app.routers.leaders import _get_live_leaderboard

    await current_user(required_scope="read")
    sorting = "pnl" if sort.lower() in ("pnl", "pnl_total") else "vol"
    period = period if period in ("all", "day") else "all"
    traders = await _get_live_leaderboard(period, sorting)
    return traders[: max(1, min(int(limit), 100))]


@server.tool()
async def get_leader_positions(wallet: str) -> dict:
    """Get a specific trader's live on-chain positions and account balance.
    Wallet address is public on-chain so this works for any address — useful
    for inspecting what a leader the user follows is currently holding.
    """
    from app.mcp.tools.markets import _validate_wallet
    from app.services.chain_reader import get_trader_detail

    await current_user(required_scope="read")
    wallet = _validate_wallet(wallet)
    detail = await asyncio.get_event_loop().run_in_executor(
        None, get_trader_detail, wallet
    )
    if not detail:
        return {"connected": False, "wallet": wallet, "message": "No on-chain account"}
    detail["connected"] = True
    return detail


@server.tool()
async def get_leader_trades(leader_wallet: str, limit: int = 50) -> list[dict]:
    """Recent fills (entries and exits) for a leader, from the trade-tracker
    DB. Newer first.
    """
    from app.db.models import Leader
    from app.mcp.tools.markets import _validate_wallet

    await current_user(required_scope="read")
    leader_wallet = _validate_wallet(leader_wallet)
    limit = max(1, min(int(limit), 200))
    sf = get_session_factory()
    async with sf() as session:
        # Find the leader_id from wallet
        leader_row = await session.execute(
            select(Leader).where(Leader.wallet_address == leader_wallet)
        )
        leader = leader_row.scalar_one_or_none()
        if not leader:
            return []
        result = await session.execute(
            select(LeaderTrade)
            .where(LeaderTrade.leader_id == leader.id)
            .order_by(desc(LeaderTrade.timestamp))
            .limit(limit)
        )
        rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "market_id": r.market_id,
            "side": r.side,
            "size": r.size,
            "price": r.price,
            "leverage": r.leverage,
            "is_close": r.is_close,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        }
        for r in rows
    ]


@server.tool()
async def get_trader_activity(limit: int = 20) -> list[dict]:
    """Recent entries / exits across the top traders being tracked. Newest
    first. Useful for spotting fresh moves the user might want to copy.
    """
    from app.db.models import TraderActivity

    await current_user(required_scope="read")
    limit = max(1, min(int(limit), 200))
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(TraderActivity)
            .order_by(desc(TraderActivity.timestamp))
            .limit(limit)
        )
        rows = result.scalars().all()
    return [
        {
            "wallet": r.wallet_address,
            "market_id": r.market_id,
            "symbol": r.symbol,
            "side": r.side,
            "size": r.size,
            "entry_price": r.entry_price,
            "activity_type": r.activity_type,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        }
        for r in rows
    ]


# ----- Authenticated tools ---------------------------------------------------


@server.tool()
async def get_my_follows() -> list[dict]:
    """List the wallets the user is currently following, with allocation
    and leverage settings.
    """
    user = await current_user()
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(WalletFollow)
            .where(
                WalletFollow.follower_wallet == user.wallet_address.lower(),
                WalletFollow.is_active == True,  # noqa: E712
            )
            .order_by(desc(WalletFollow.created_at))
        )
        rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "leader_wallet": r.leader_wallet,
            "allocation_usd": r.allocation_usd,
            "max_leverage": r.max_leverage,
            "auto_copy": r.auto_copy,
            "sl_pct": r.sl_pct,
            "tp_pct": r.tp_pct,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@server.tool()
async def get_copy_stats() -> dict:
    """Aggregated copy-trading stats for the user: PnL per leader, win rate,
    total ROI, copy count.
    """
    from app.services.copy_position_service import get_copy_stats as _impl

    user = await current_user()
    return await _impl(user.wallet_address.lower())


@server.tool()
async def get_copy_history(limit: int = 50) -> list[dict]:
    """Recent copy-trade lifecycle events (positions opened/closed by the
    auto-copy engine on the user's behalf).
    """
    from app.services.copy_position_service import get_all_copies
    user = await current_user()
    return await get_all_copies(user.wallet_address.lower(), "all", 0, max(1, min(limit, 200)))
