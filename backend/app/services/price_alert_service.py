"""Monitors active price alerts and triggers them when price crosses target.

Subscribes to EVENT_MARKET_STATE_UPDATE from the event bus.
On trigger: updates DB status, sends Telegram notification.
"""
from datetime import datetime

from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.models import PriceAlertDB, TelegramLink
from app.services.notification import event_bus, EVENT_MARKET_STATE_UPDATE
from app.utils.logger import get_logger

logger = get_logger(__name__)

_started = False


async def start():
    global _started
    if _started:
        return
    event_bus.subscribe(EVENT_MARKET_STATE_UPDATE, _on_market_update)
    _started = True
    logger.info("Price alert service started")


async def _on_market_update(data: dict):
    """Called on every market state update. Check active alerts for this market."""
    market_id = data.get("market_id")
    mark_price = data.get("mark_price", 0)
    if not market_id or mark_price <= 0:
        return

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(
            select(PriceAlertDB).where(
                PriceAlertDB.status == "active",
                PriceAlertDB.market_id == market_id,
            )
        )
        alerts = result.scalars().all()
        if not alerts:
            return

        for alert in alerts:
            hit = False
            if alert.condition == "above" and mark_price >= alert.target_price:
                hit = True
            elif alert.condition == "below" and mark_price <= alert.target_price:
                hit = True

            if hit:
                alert.status = "triggered"
                alert.triggered_at = datetime.utcnow()
                await session.commit()

                logger.info(
                    "Price alert triggered: %s %s $%.2f (current $%.2f) for user %d",
                    alert.symbol, alert.condition, alert.target_price, mark_price, alert.user_id,
                )

                # Send Telegram notification if linked
                try:
                    tg_result = await session.execute(
                        select(TelegramLink).where(
                            TelegramLink.user_id == alert.user_id,
                            TelegramLink.is_active == True,
                        )
                    )
                    tg_link = tg_result.scalar_one_or_none()
                    if tg_link and tg_link.chat_id:
                        from app.services.telegram_bot import send_notification
                        msg = (
                            f"<b>Price Alert Triggered</b>\n\n"
                            f"<b>{alert.symbol}</b> is now <b>${mark_price:,.2f}</b>\n"
                            f"Alert: {alert.condition} ${alert.target_price:,.2f}"
                        )
                        await send_notification(tg_link.chat_id, msg)
                except Exception:
                    logger.exception("Failed to send Telegram for price alert %d", alert.id)
