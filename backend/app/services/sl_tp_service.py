"""Stop-Loss / Take-Profit monitoring service.

Subscribes to EVENT_MARKET_STATE_UPDATE. On each price tick,
checks all active stop orders for that market and triggers
close orders via the Perpl trading WS when price crosses the trigger.
"""
import asyncio
from datetime import datetime

from sqlalchemy import select, update
from app.db.database import get_session_factory
from app.db.models import StopOrder, User
from app.services.notification import event_bus, EVENT_MARKET_STATE_UPDATE
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Cache active orders in memory for fast checks (refreshed from DB periodically)
_active_orders: dict[int, list[dict]] = {}  # market_id -> list of orders
_last_refresh = 0.0
REFRESH_INTERVAL = 10  # seconds - reload from DB


async def start():
    """Subscribe to price updates."""
    event_bus.subscribe(EVENT_MARKET_STATE_UPDATE, _on_price_update)
    await _refresh_cache()
    logger.info("SL/TP service started")


async def _refresh_cache():
    """Load all active stop orders from DB into memory cache."""
    global _active_orders, _last_refresh
    import time
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(StopOrder).where(StopOrder.status == "active")
        )
        orders = result.scalars().all()

    cache: dict[int, list[dict]] = {}
    for o in orders:
        if o.market_id not in cache:
            cache[o.market_id] = []
        cache[o.market_id].append({
            "id": o.id,
            "user_id": o.user_id,
            "wallet_address": o.wallet_address,
            "market_id": o.market_id,
            "side": o.side,
            "order_type": o.order_type,
            "trigger_price": o.trigger_price,
            "size": o.size,
        })
    _active_orders = cache
    _last_refresh = time.time()


async def _on_price_update(state: dict):
    """Called on every market state update (every 3s per market)."""
    import time
    market_id = state.get("market_id")
    mark_price = state.get("mark_price", 0)
    if not market_id or not mark_price:
        return

    # Periodically refresh cache from DB
    if time.time() - _last_refresh > REFRESH_INTERVAL:
        await _refresh_cache()

    orders = _active_orders.get(market_id, [])
    if not orders:
        return

    triggered = []
    for order in orders:
        should_trigger = False

        if order["side"] == "long":
            # Long position: SL triggers when price drops below trigger, TP triggers when price rises above
            if order["order_type"] == "sl" and mark_price <= order["trigger_price"]:
                should_trigger = True
            elif order["order_type"] == "tp" and mark_price >= order["trigger_price"]:
                should_trigger = True
        elif order["side"] == "short":
            # Short position: SL triggers when price rises above trigger, TP triggers when price drops below
            if order["order_type"] == "sl" and mark_price >= order["trigger_price"]:
                should_trigger = True
            elif order["order_type"] == "tp" and mark_price <= order["trigger_price"]:
                should_trigger = True

        if should_trigger:
            triggered.append(order)

    for order in triggered:
        asyncio.create_task(_execute_stop_order(order, mark_price))


async def _execute_stop_order(order: dict, mark_price: float):
    """Execute a triggered stop order by closing the position."""
    order_id = order["id"]
    logger.info(
        "SL/TP triggered: id=%d %s %s market=%d trigger=%.2f mark=%.2f",
        order_id, order["order_type"].upper(), order["side"],
        order["market_id"], order["trigger_price"], mark_price,
    )

    # Remove from cache immediately to prevent double-trigger
    market_orders = _active_orders.get(order["market_id"], [])
    _active_orders[order["market_id"]] = [o for o in market_orders if o["id"] != order_id]

    sf = get_session_factory()
    try:
        # Mark as triggered in DB
        async with sf() as session:
            await session.execute(
                update(StopOrder)
                .where(StopOrder.id == order_id, StopOrder.status == "active")
                .values(status="triggered", triggered_at=datetime.utcnow())
            )
            await session.commit()

        # Broadcast to frontend so UI updates
        from app.ws.client_feed import client_manager
        await client_manager.broadcast("sl_tp_triggered", {
            "id": order_id,
            "market_id": order["market_id"],
            "order_type": order["order_type"],
            "side": order["side"],
            "trigger_price": order["trigger_price"],
            "mark_price": mark_price,
            "wallet_address": order["wallet_address"],
        })

        logger.info("SL/TP executed: id=%d — position close broadcast sent", order_id)

    except Exception:
        logger.exception("Failed to execute SL/TP order id=%d", order_id)
        async with sf() as session:
            await session.execute(
                update(StopOrder)
                .where(StopOrder.id == order_id)
                .values(status="failed", error="Execution error")
            )
            await session.commit()


async def add_order(user_id: int, wallet_address: str, market_id: int, side: str,
                    order_type: str, trigger_price: float, size: float | None = None,
                    source: str = "manual") -> dict:
    """Create a new SL/TP order."""
    sf = get_session_factory()
    async with sf() as session:
        order = StopOrder(
            user_id=user_id,
            wallet_address=wallet_address.lower(),
            market_id=market_id,
            side=side,
            order_type=order_type,
            trigger_price=trigger_price,
            size=size,
            source=source,
            status="active",
            created_at=datetime.utcnow(),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

    # Update cache
    if market_id not in _active_orders:
        _active_orders[market_id] = []
    _active_orders[market_id].append({
        "id": order.id,
        "user_id": user_id,
        "wallet_address": wallet_address.lower(),
        "market_id": market_id,
        "side": side,
        "order_type": order_type,
        "trigger_price": trigger_price,
        "size": size,
    })

    logger.info("SL/TP created: id=%d %s %s mkt=%d @ %.2f", order.id, order_type, side, market_id, trigger_price)
    return {
        "id": order.id,
        "market_id": market_id,
        "side": side,
        "order_type": order_type,
        "trigger_price": trigger_price,
        "size": size,
        "status": "active",
        "source": source,
    }


async def cancel_order(order_id: int, user_id: int) -> bool:
    """Cancel an active SL/TP order."""
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(StopOrder).where(
                StopOrder.id == order_id,
                StopOrder.user_id == user_id,
                StopOrder.status == "active",
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            return False

        order.status = "cancelled"
        await session.commit()

        # Remove from cache
        market_orders = _active_orders.get(order.market_id, [])
        _active_orders[order.market_id] = [o for o in market_orders if o["id"] != order_id]

    logger.info("SL/TP cancelled: id=%d", order_id)
    return True


async def get_user_orders(user_id: int, status: str = "active") -> list[dict]:
    """Get all SL/TP orders for a user."""
    sf = get_session_factory()
    async with sf() as session:
        stmt = select(StopOrder).where(StopOrder.user_id == user_id)
        if status:
            stmt = stmt.where(StopOrder.status == status)
        stmt = stmt.order_by(StopOrder.created_at.desc())
        result = await session.execute(stmt)
        orders = result.scalars().all()

    return [
        {
            "id": o.id,
            "market_id": o.market_id,
            "side": o.side,
            "order_type": o.order_type,
            "trigger_price": o.trigger_price,
            "size": o.size,
            "status": o.status,
            "source": o.source,
            "error": o.error,
            "created_at": o.created_at.isoformat() if o.created_at else None,
            "triggered_at": o.triggered_at.isoformat() if o.triggered_at else None,
        }
        for o in orders
    ]
