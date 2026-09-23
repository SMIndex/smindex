"""Simulated (paper) copy-position lifecycle for copy v1.

Operates ONLY on the v1 `copy_paper_positions` table. No real orders. All money
math uses Decimal. At most one OPEN row per (subscription_id, market_id).
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.copy_models import CopyPaperPosition
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _D(x) -> Decimal:
    if x is None:
        return Decimal(0)
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _pnl(side: str, entry, close, size) -> Decimal:
    """Realized PnL for `size` units. Long: (close-entry)*size; short: inverse."""
    entry, close, size = _D(entry), _D(close), _D(size)
    if side == "long":
        return (close - entry) * size
    return (entry - close) * size


def _f(x):
    return float(x) if x is not None else None


def _dict(p: CopyPaperPosition, mark_price=None) -> dict:
    # unrealized computed at read-time from a live mark when available, else 0.
    unreal = 0.0
    if p.status == "open" and mark_price:
        unreal = float(_pnl(p.side, p.entry_price, mark_price, p.size))
    return {
        "id": p.id,
        "subscription_id": p.subscription_id,
        "follower_wallet": p.follower_wallet,
        "trader_wallet": p.trader_wallet,
        "leader_event_id": p.leader_event_id,
        "mode": p.mode,
        "market_id": p.market_id,
        "symbol": p.symbol,
        "side": p.side,
        "entry_price": _f(p.entry_price),
        "size": _f(p.size),
        "leverage": _f(p.leverage),
        "allocation_usd": _f(p.allocation_usd),
        "status": p.status,
        "close_price": _f(p.close_price),
        "realized_pnl": _f(p.realized_pnl),
        "unrealized_pnl": round(unreal, 8),
        "mark_price": _f(mark_price) if mark_price else None,
        "close_reason": p.close_reason,
        "opened_at": p.opened_at.isoformat() if p.opened_at else None,
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
    }


async def _load_open(session, subscription_id: int, market_id: int) -> CopyPaperPosition | None:
    return (await session.execute(
        select(CopyPaperPosition).where(
            CopyPaperPosition.subscription_id == subscription_id,
            CopyPaperPosition.market_id == market_id,
            CopyPaperPosition.status == "open",
        )
    )).scalar_one_or_none()


async def get_open_paper_position(subscription_id: int, market_id: int) -> dict | None:
    sf = get_session_factory()
    async with sf() as session:
        p = await _load_open(session, subscription_id, market_id)
        return _dict(p) if p else None


async def open_paper_position(
    *, subscription_id: int, follower_wallet: str, trader_wallet: str,
    leader_event_id: int | None, market_id: int, symbol: str, side: str,
    entry_price, size, leverage, allocation_usd,
) -> dict:
    sf = get_session_factory()
    async with sf() as session:
        existing = await _load_open(session, subscription_id, market_id)
        if existing:
            # Already open — treat as a no-op open (engine should call increase).
            return _dict(existing)
        p = CopyPaperPosition(
            subscription_id=subscription_id,
            follower_wallet=follower_wallet.lower(),
            trader_wallet=trader_wallet.lower(),
            leader_event_id=leader_event_id,
            mode="paper",
            market_id=market_id, symbol=symbol, side=side,
            entry_price=_D(entry_price), size=_D(size),
            leverage=_D(leverage), allocation_usd=_D(allocation_usd),
            status="open", realized_pnl=Decimal(0), unrealized_pnl=Decimal(0),
            opened_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return _dict(p)


async def increase_paper_position(subscription_id: int, market_id: int, add_size, price) -> dict | None:
    sf = get_session_factory()
    async with sf() as session:
        p = await _load_open(session, subscription_id, market_id)
        if not p:
            return None
        old_size, add_size = _D(p.size), _D(add_size)
        new_size = old_size + add_size
        if new_size > 0:
            # size-weighted average entry
            p.entry_price = (_D(p.entry_price) * old_size + _D(price) * add_size) / new_size
        p.size = new_size
        p.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(p)
        return _dict(p)


async def reduce_paper_position(subscription_id: int, market_id: int, reduce_size, price,
                                reason: str = "leader_reduce") -> dict | None:
    sf = get_session_factory()
    async with sf() as session:
        p = await _load_open(session, subscription_id, market_id)
        if not p:
            return None
        reduce_size = min(_D(reduce_size), _D(p.size))
        realized = _pnl(p.side, p.entry_price, price, reduce_size)
        p.realized_pnl = _D(p.realized_pnl) + realized
        p.size = _D(p.size) - reduce_size
        if p.size <= 0:
            p.status = "closed"
            p.close_price = _D(price)
            p.closed_at = datetime.utcnow()
            p.close_reason = reason
        p.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(p)
        return _dict(p)


async def close_paper_position(subscription_id: int, market_id: int, price,
                               reason: str = "leader_close") -> dict | None:
    sf = get_session_factory()
    async with sf() as session:
        p = await _load_open(session, subscription_id, market_id)
        if not p:
            return None
        realized = _pnl(p.side, p.entry_price, price, p.size)
        p.realized_pnl = _D(p.realized_pnl) + realized
        p.status = "liquidated" if reason == "liquidation" else "closed"
        p.close_price = _D(price)
        p.closed_at = datetime.utcnow()
        p.close_reason = reason
        p.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(p)
        return _dict(p)


async def list_positions(follower_wallet: str, status: str = "all",
                         subscription_id: int | None = None,
                         trader_wallet: str | None = None) -> list[dict]:
    """Read paper positions with optional filters. Unrealized PnL computed from
    the live mark cache when available (else 0)."""
    sf = get_session_factory()
    async with sf() as session:
        stmt = select(CopyPaperPosition).where(
            CopyPaperPosition.follower_wallet == follower_wallet.lower()
        )
        if status != "all":
            stmt = stmt.where(CopyPaperPosition.status == status)
        if subscription_id is not None:
            stmt = stmt.where(CopyPaperPosition.subscription_id == subscription_id)
        if trader_wallet:
            stmt = stmt.where(CopyPaperPosition.trader_wallet == trader_wallet.lower())
        stmt = stmt.order_by(desc(CopyPaperPosition.opened_at))
        rows = (await session.execute(stmt)).scalars().all()

    # best-effort live marks for unrealized PnL
    marks: dict[int, float] = {}
    try:
        from app.services.ws_manager import ws_manager
        if ws_manager:
            for p in rows:
                if p.status == "open" and p.market_id in ws_manager.market_state_cache:
                    marks[p.market_id] = ws_manager.market_state_cache[p.market_id].get("mark_price")
    except Exception:
        pass
    return [_dict(p, marks.get(p.market_id)) for p in rows]
