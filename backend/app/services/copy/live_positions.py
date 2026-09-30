"""Live (real) copied-position lifecycle for live_manual copy.

A CopyLivePosition is created when a live copy OPEN order FILLS, and is updated /
closed when the follower confirms a close/reduce (the real order is placed
CLIENT-SIDE, wallet-signed). This module NEVER places an order — it only tracks.

Leader close/reduce events set a SUGGESTION on matching open positions
(`close_suggested`) so the UI can prompt the follower to confirm — never auto.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, desc

from app.db.database import get_session_factory
from app.db.copy_models import CopyLivePosition, CopyOrder
from app.services.copy import audit
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _D(x) -> Decimal:
    if x is None:
        return Decimal(0)
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _f(x):
    return float(x) if x is not None else None


def _realized(side: str, entry, close, size) -> Decimal:
    entry, close, size = _D(entry), _D(close), _D(size)
    return (close - entry) * size if side == "long" else (entry - close) * size


def _dict(p: CopyLivePosition) -> dict:
    return {
        "id": p.id,
        "copy_order_id": p.copy_order_id,
        "subscription_id": p.subscription_id,
        "follower_wallet": p.follower_wallet,
        "trader_wallet": p.trader_wallet,
        "leader_event_id": p.leader_event_id,
        "leader_position_id": p.leader_position_id,
        "follower_market_id": p.follower_market_id,
        "symbol": p.symbol,
        "side": p.side,
        "status": p.status,
        "entry_price": _f(p.entry_price),
        "entry_size": _f(p.entry_size),
        "entry_margin": _f(p.entry_margin),
        "entry_leverage": _f(p.entry_leverage),
        "current_size": _f(p.current_size),
        "close_price": _f(p.close_price),
        "realized_pnl": _f(p.realized_pnl),
        "triggers_failed": bool(getattr(p, "triggers_failed", False)),
        "close_suggested": bool(p.close_suggested),
        "close_suggestion_type": p.close_suggestion_type,
        "suggested_at": p.suggested_at.isoformat() if p.suggested_at else None,
        "opened_at": p.opened_at.isoformat() if p.opened_at else None,
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        "close_reason": p.close_reason,
    }


async def open_from_order(order: dict, triggers_failed: bool = False) -> dict:
    """Create a live position from a FILLED open copy_order. Idempotent on
    copy_order_id (a duplicate fill returns the existing position, no duplicate).

    HARD RULE: a live position must be backed by a real Perpl confirmation. Refuse to
    create one unless the order carries a real Perpl order id or fill id — this prevents
    phantom 'live' positions from an unconfirmed client PATCH.
    """
    if not (order.get("perpl_order_id") or order.get("perpl_fill_id")):
        raise ValueError(
            "open_from_order requires a real Perpl order/fill id; refusing to create an "
            "unconfirmed live position"
        )
    sf = get_session_factory()
    async with sf() as session:
        existing = (await session.execute(
            select(CopyLivePosition).where(CopyLivePosition.copy_order_id == order["id"])
        )).scalar_one_or_none()
        if existing:
            return _dict(existing)

        size = order.get("fill_size") if order.get("fill_size") is not None else order.get("intended_size")
        price = order.get("fill_price") if order.get("fill_price") is not None else order.get("intended_price")
        p = CopyLivePosition(
            copy_order_id=order["id"],
            subscription_id=order.get("subscription_id"),
            follower_wallet=order["follower_wallet"].lower(),
            trader_wallet=order["trader_wallet"].lower(),
            leader_event_id=order.get("leader_event_id"),
            follower_market_id=order["market_id"],
            symbol=order["symbol"],
            side=order["side"],
            status="open",
            triggers_failed=triggers_failed,
            entry_price=_D(price),
            entry_size=_D(size),
            entry_margin=_D(order.get("allocation_usd")),
            entry_leverage=_D(order.get("leverage")),
            current_size=_D(size),
            opened_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(p)
        try:
            await session.commit()
            await session.refresh(p)
            return _dict(p)
        except Exception:
            await session.rollback()
            dup = (await session.execute(
                select(CopyLivePosition).where(CopyLivePosition.copy_order_id == order["id"])
            )).scalar_one_or_none()
            if dup:
                return _dict(dup)
            raise


async def get_position(position_id: int, follower_wallet: str) -> dict | None:
    sf = get_session_factory()
    async with sf() as session:
        p = (await session.execute(
            select(CopyLivePosition).where(
                CopyLivePosition.id == position_id,
                CopyLivePosition.follower_wallet == follower_wallet.lower(),
            )
        )).scalar_one_or_none()
        return _dict(p) if p else None


async def list_positions(follower_wallet: str, status: str = "all") -> list[dict]:
    sf = get_session_factory()
    async with sf() as session:
        stmt = select(CopyLivePosition).where(
            CopyLivePosition.follower_wallet == follower_wallet.lower()
        )
        if status == "open":
            stmt = stmt.where(CopyLivePosition.status.in_(["open", "partially_closed"]))
        elif status != "all":
            stmt = stmt.where(CopyLivePosition.status == status)
        rows = (await session.execute(stmt.order_by(desc(CopyLivePosition.opened_at)))).scalars().all()
        return [_dict(p) for p in rows]


async def has_pending_close(position_id: int) -> bool:
    """True if a close/reduce order for this position is already submitted (not yet
    resolved) — prevents duplicate close orders for the same position."""
    sf = get_session_factory()
    async with sf() as session:
        row = (await session.execute(
            select(CopyOrder).where(
                CopyOrder.action.in_(["close", "reduce"]),
                CopyOrder.status == "submitted",
                CopyOrder.idempotency_key.like(f"%:pos:{position_id}:%"),
            )
        )).scalar_one_or_none()
        return row is not None


async def apply_close_result(position_id: int, follower_wallet: str, *, action: str,
                             close_price=None, reduce_size=None, reason: str = "manual") -> dict | None:
    """After a real close/reduce order FILLS: update the live position.
    action=close -> closed; action=reduce -> partially_closed (or closed if drained)."""
    sf = get_session_factory()
    async with sf() as session:
        p = (await session.execute(
            select(CopyLivePosition).where(
                CopyLivePosition.id == position_id,
                CopyLivePosition.follower_wallet == follower_wallet.lower(),
            )
        )).scalar_one_or_none()
        if not p:
            return None

        cur = _D(p.current_size)
        if action == "reduce" and reduce_size is not None:
            closed_qty = min(_D(reduce_size), cur)
        else:  # full close
            closed_qty = cur

        if close_price is not None and closed_qty > 0:
            p.realized_pnl = _D(p.realized_pnl) + _realized(p.side, p.entry_price, close_price, closed_qty)
            p.close_price = _D(close_price)
        p.current_size = cur - closed_qty

        if p.current_size <= 0 or action == "close":
            p.status = "closed"
            p.current_size = Decimal(0)
            p.closed_at = datetime.utcnow()
        else:
            p.status = "partially_closed"
        p.close_reason = reason
        # the suggestion (if any) is now acted upon
        p.close_suggested = False
        p.close_suggestion_type = None
        p.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(p)
        return _dict(p)


async def handle_leader_event(event: dict) -> None:
    """Leader closed/reduced -> flag a SUGGESTION on matching OPEN live positions for
    that trader/market/side (all followers). NEVER places an order. Best-effort."""
    etype = event.get("event_type")
    if etype not in ("closed", "reduced", "liquidated"):
        return
    suggestion = "reduce" if etype == "reduced" else "close"
    try:
        sf = get_session_factory()
        async with sf() as session:
            rows = (await session.execute(
                select(CopyLivePosition).where(
                    CopyLivePosition.trader_wallet == event["trader_wallet"].lower(),
                    CopyLivePosition.follower_market_id == event["market_id"],
                    CopyLivePosition.side == event["side"],
                    CopyLivePosition.status.in_(["open", "partially_closed"]),
                )
            )).scalars().all()
            now = datetime.utcnow()
            for p in rows:
                p.close_suggested = True
                p.close_suggestion_type = suggestion
                p.suggested_at = now
                p.suggested_event_id = event.get("id")
                p.updated_at = now
            await session.commit()
        for p in rows:
            await audit.write_audit(
                p.follower_wallet, "live_close_suggested", entity_type="copy_live_position",
                entity_id=p.id,
                detail={"suggestion": suggestion, "leader_event_id": event.get("id"),
                        "trader_wallet": event["trader_wallet"].lower(), "market_id": event["market_id"]},
            )
    except Exception:
        logger.exception("handle_leader_event (live positions) failed for %s", event.get("id"))
