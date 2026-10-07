# =============================================================================
# LEGACY (copy v0) — ACTIVE today (powers /copy/positions, /copy/stats for manual
# copies). Will be rebuilt as services/copy/positions.py in copy v1 (adds
# subscription_id / mode / leader_event_id / close_reason + leader-close sync).
# DO NOT extend this in place; build new copy logic under copy v1.
# =============================================================================
"""Copy position lifecycle tracking.

Tracks which follower positions came from which leader,
with PnL attribution and stats aggregation.
"""
from datetime import datetime

from sqlalchemy import select, func, and_, desc
from app.db.database import get_session_factory
from app.db.models import CopyPosition, WalletFollow
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def create_copy_position(
    follower_wallet: str, leader_wallet: str, market_id: int, symbol: str,
    side: str, entry_price: float, size: float, leverage: float,
    allocation_usd: float, source: str = "manual",
) -> dict:
    """Create a copy_position record after successful trade execution."""
    sf = get_session_factory()
    async with sf() as session:
        pos = CopyPosition(
            follower_wallet=follower_wallet.lower(),
            leader_wallet=leader_wallet.lower(),
            market_id=market_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            size=size,
            leverage=leverage,
            allocation_usd=allocation_usd,
            source=source,
            status="open",
            opened_at=datetime.utcnow(),
        )
        session.add(pos)
        await session.commit()
        await session.refresh(pos)

    logger.info("Copy position created: id=%d %s %s %s from %s", pos.id, symbol, side, follower_wallet[:10], leader_wallet[:10])
    return _to_dict(pos)


async def close_copy_position(position_id: int, follower_wallet: str, close_price: float) -> dict | None:
    """Close a copy position and calculate realized PnL."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyPosition).where(
                CopyPosition.id == position_id,
                CopyPosition.follower_wallet == follower_wallet.lower(),
                CopyPosition.status == "open",
            )
        )
        pos = result.scalar_one_or_none()
        if not pos:
            return None

        if pos.side == "long":
            realized_pnl = (close_price - pos.entry_price) * pos.size
        else:
            realized_pnl = (pos.entry_price - close_price) * pos.size

        pos.status = "closed"
        pos.close_price = close_price
        pos.realized_pnl = round(realized_pnl, 2)
        pos.closed_at = datetime.utcnow()
        await session.commit()
        await session.refresh(pos)

    logger.info("Copy position closed: id=%d pnl=%.2f", position_id, realized_pnl)
    return _to_dict(pos)


async def get_open_copies(follower_wallet: str) -> list[dict]:
    """Get all open copy positions for a follower with live PnL."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyPosition).where(
                CopyPosition.follower_wallet == follower_wallet.lower(),
                CopyPosition.status == "open",
            ).order_by(desc(CopyPosition.opened_at))
        )
        positions = result.scalars().all()

    # Compute live PnL using cached mark prices
    from app.services.ws_manager import ws_manager
    items = []
    for pos in positions:
        d = _to_dict(pos)
        mark_price = pos.entry_price  # fallback
        if ws_manager and pos.market_id in ws_manager.market_state_cache:
            mark_price = ws_manager.market_state_cache[pos.market_id].get("mark_price", mark_price)
        if pos.side == "long":
            d["unrealized_pnl"] = round((mark_price - pos.entry_price) * pos.size, 2)
        else:
            d["unrealized_pnl"] = round((pos.entry_price - mark_price) * pos.size, 2)
        d["mark_price"] = mark_price
        items.append(d)
    return items


async def get_all_copies(follower_wallet: str, status: str = "all", skip: int = 0, limit: int = 50) -> list[dict]:
    """Get copy positions with optional status filter."""
    sf = get_session_factory()
    async with sf() as session:
        stmt = select(CopyPosition).where(CopyPosition.follower_wallet == follower_wallet.lower())
        if status != "all":
            stmt = stmt.where(CopyPosition.status == status)
        stmt = stmt.order_by(desc(CopyPosition.opened_at)).offset(skip).limit(limit)
        result = await session.execute(stmt)
        positions = result.scalars().all()

    from app.services.ws_manager import ws_manager
    items = []
    for pos in positions:
        d = _to_dict(pos)
        if pos.status == "open" and ws_manager and pos.market_id in ws_manager.market_state_cache:
            mark_price = ws_manager.market_state_cache[pos.market_id].get("mark_price", pos.entry_price)
            d["unrealized_pnl"] = round(
                (mark_price - pos.entry_price) * pos.size if pos.side == "long"
                else (pos.entry_price - mark_price) * pos.size, 2
            )
            d["mark_price"] = mark_price
        items.append(d)
    return items


async def get_copy_stats(follower_wallet: str) -> dict:
    """Aggregated copy trading stats for a follower."""
    wallet = follower_wallet.lower()
    sf = get_session_factory()
    async with sf() as session:
        # Active copies count
        active_result = await session.execute(
            select(func.count(CopyPosition.id)).where(
                CopyPosition.follower_wallet == wallet,
                CopyPosition.status == "open",
            )
        )
        active_count = active_result.scalar() or 0

        # Closed copies with PnL
        closed_result = await session.execute(
            select(
                CopyPosition.leader_wallet,
                func.count(CopyPosition.id).label("trades"),
                func.sum(CopyPosition.realized_pnl).label("pnl"),
                func.sum(func.IF(CopyPosition.realized_pnl > 0, 1, 0)).label("wins"),
            ).where(
                CopyPosition.follower_wallet == wallet,
                CopyPosition.status == "closed",
            ).group_by(CopyPosition.leader_wallet)
        )
        # MySQL doesn't support func.IF easily via SQLAlchemy, use case
        pass

    # Simpler approach: fetch all closed, compute in Python
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyPosition).where(
                CopyPosition.follower_wallet == wallet,
                CopyPosition.status == "closed",
            )
        )
        closed = result.scalars().all()

    per_leader: dict[str, dict] = {}
    total_pnl = 0.0
    for pos in closed:
        pnl = pos.realized_pnl or 0
        total_pnl += pnl
        lw = pos.leader_wallet
        if lw not in per_leader:
            per_leader[lw] = {"leader_wallet": lw, "pnl": 0, "trades": 0, "wins": 0}
        per_leader[lw]["pnl"] += pnl
        per_leader[lw]["trades"] += 1
        if pnl > 0:
            per_leader[lw]["wins"] += 1

    # Add open PnL
    open_copies = await get_open_copies(wallet)
    open_pnl = sum(c.get("unrealized_pnl", 0) for c in open_copies)

    leader_stats = []
    for ls in per_leader.values():
        ls["pnl"] = round(ls["pnl"], 2)
        ls["win_rate"] = round(ls["wins"] / ls["trades"] * 100, 1) if ls["trades"] > 0 else 0
        leader_stats.append(ls)

    return {
        "total_realized_pnl": round(total_pnl, 2),
        "total_unrealized_pnl": round(open_pnl, 2),
        "total_pnl": round(total_pnl + open_pnl, 2),
        "active_copies": active_count,
        "closed_copies": len(closed),
        "per_leader": sorted(leader_stats, key=lambda x: x["pnl"], reverse=True),
    }


async def get_follower_count(leader_wallet: str) -> int:
    """Count active followers for a leader."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(func.count(WalletFollow.id)).where(
                WalletFollow.leader_wallet == leader_wallet.lower(),
                WalletFollow.is_active == True,
            )
        )
        return result.scalar() or 0


async def get_followers_list(leader_wallet: str) -> list[dict]:
    """Get list of followers for a leader."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(WalletFollow).where(
                WalletFollow.leader_wallet == leader_wallet.lower(),
                WalletFollow.is_active == True,
            ).order_by(desc(WalletFollow.created_at))
        )
        follows = result.scalars().all()

    return [
        {
            "follower_wallet": f.follower_wallet,
            "allocation_usd": f.allocation_usd,
            "max_leverage": f.max_leverage,
            "auto_copy": f.auto_copy,
            "since": f.created_at.isoformat() if f.created_at else None,
        }
        for f in follows
    ]


async def get_open_copies_for_leader_market(leader_wallet: str, market_id: int) -> list[dict]:
    """Get all open copy positions for a specific leader+market (for close sync)."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(CopyPosition).where(
                CopyPosition.leader_wallet == leader_wallet.lower(),
                CopyPosition.market_id == market_id,
                CopyPosition.status == "open",
            )
        )
        positions = result.scalars().all()
    return [_to_dict(pos) for pos in positions]


def _to_dict(pos: CopyPosition) -> dict:
    return {
        "id": pos.id,
        "follower_wallet": pos.follower_wallet,
        "leader_wallet": pos.leader_wallet,
        "market_id": pos.market_id,
        "symbol": pos.symbol,
        "side": pos.side,
        "entry_price": pos.entry_price,
        "size": pos.size,
        "leverage": pos.leverage,
        "allocation_usd": pos.allocation_usd,
        "status": pos.status,
        "close_price": pos.close_price,
        "realized_pnl": pos.realized_pnl,
        "source": pos.source,
        "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
    }
