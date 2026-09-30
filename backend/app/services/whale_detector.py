from datetime import datetime

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import WhaleAlert as WhaleAlertModel
from app.models.whale import WhaleAlert, OIDivergence
from app.services.notification import event_bus, EVENT_WHALE_ALERT
from app.utils.logger import get_logger

logger = get_logger(__name__)


class WhaleDetector:
    OI_DIVERGENCE_THRESHOLD_PCT: float = 2.0

    def __init__(self) -> None:
        self._thresholds: dict[int, float] = {}

    def get_threshold(self, market_id: int) -> float:
        return self._thresholds.get(market_id, settings.WHALE_THRESHOLD_USD)

    def set_threshold(self, market_id: int, threshold_usd: float) -> None:
        self._thresholds[market_id] = threshold_usd

    def _calculate_severity(self, size_usd: float, threshold: float) -> str:
        ratio = size_usd / threshold
        if ratio >= 10:
            return "extreme"
        elif ratio >= 3:
            return "high"
        else:
            return "medium"

    async def _emit_alert(self, alert: WhaleAlert) -> None:
        from app.ws.client_feed import client_manager

        async_session = get_session_factory()
        async with async_session() as session:
            alert_row = WhaleAlertModel(
                market_id=alert.market_id,
                alert_type=alert.alert_type,
                side=alert.side,
                size_usd=alert.size_usd,
                price=alert.price,
                severity=alert.severity,
                details=alert.details,
                timestamp=alert.timestamp,
            )
            session.add(alert_row)
            await session.commit()
            await session.refresh(alert_row)

        alert_data = alert.model_dump(mode="json")
        alert_data["id"] = alert_row.id

        await client_manager.broadcast(
            "whale_alerts", alert_data, market_id=alert.market_id
        )

        await event_bus.emit(EVENT_WHALE_ALERT, alert_data)

        logger.info(
            "Whale alert: %s market=%d size=$%.0f severity=%s",
            alert.alert_type,
            alert.market_id,
            alert.size_usd,
            alert.severity,
        )

    async def check_orderbook(self, market_id: int, orderbook_data: dict) -> None:
        threshold = self.get_threshold(market_id)

        for side_key, side_label in [("bids", "buy"), ("asks", "sell")]:
            orders = orderbook_data.get(side_key, [])
            for order in orders:
                price = float(order.get("price", 0))
                size = float(order.get("size", 0))
                size_usd = price * size

                if size_usd >= threshold:
                    severity = self._calculate_severity(size_usd, threshold)
                    alert = WhaleAlert(
                        market_id=market_id,
                        alert_type="large_order",
                        side=side_label,
                        size_usd=size_usd,
                        price=price,
                        severity=severity,
                        details={
                            "size": size,
                            "price": price,
                            "side": side_label,
                            "source": "orderbook",
                        },
                        timestamp=datetime.utcnow(),
                    )
                    await self._emit_alert(alert)

    async def check_trade(self, market_id: int, trade_data: dict) -> None:
        threshold = self.get_threshold(market_id)

        price = float(trade_data.get("price", 0))
        size = float(trade_data.get("size", 0))
        size_usd = price * size

        if size_usd >= threshold:
            side = trade_data.get("side", "unknown")
            severity = self._calculate_severity(size_usd, threshold)
            alert = WhaleAlert(
                market_id=market_id,
                alert_type="large_fill",
                side=side,
                size_usd=size_usd,
                price=price,
                severity=severity,
                details={
                    "size": size,
                    "price": price,
                    "side": side,
                    "trade_id": trade_data.get("id"),
                    "source": "trade_feed",
                },
                timestamp=datetime.utcnow(),
            )
            await self._emit_alert(alert)

    async def check_oi_divergence(
        self,
        market_id: int,
        prev_state: dict,
        curr_state: dict,
    ) -> None:
        prev_oi = float(prev_state.get("open_interest", 0))
        curr_oi = float(curr_state.get("open_interest", 0))
        prev_price = float(prev_state.get("last_price", 0))
        curr_price = float(curr_state.get("last_price", 0))

        if prev_oi <= 0 or prev_price <= 0:
            return

        oi_change_pct = ((curr_oi - prev_oi) / prev_oi) * 100
        price_change_pct = ((curr_price - prev_price) / prev_price) * 100

        if abs(oi_change_pct) < self.OI_DIVERGENCE_THRESHOLD_PCT:
            return

        is_divergence = False
        direction = ""

        if oi_change_pct > 0 and price_change_pct < -0.5:
            is_divergence = True
            direction = "bearish_divergence"
        elif oi_change_pct > 0 and price_change_pct > 0.5:
            pass
        elif oi_change_pct < 0 and price_change_pct > 0.5:
            is_divergence = True
            direction = "bullish_divergence"

        if not is_divergence:
            return

        threshold = self.get_threshold(market_id)
        size_usd = abs(curr_oi - prev_oi) * curr_price
        severity = self._calculate_severity(
            max(size_usd, threshold), threshold
        )

        divergence = OIDivergence(
            market_id=market_id,
            oi_change_pct=round(oi_change_pct, 2),
            price_change_pct=round(price_change_pct, 2),
            direction=direction,
            timestamp=datetime.utcnow(),
        )

        alert = WhaleAlert(
            market_id=market_id,
            alert_type="oi_divergence",
            side=None,
            size_usd=size_usd,
            price=curr_price,
            severity=severity,
            details={
                "oi_change_pct": divergence.oi_change_pct,
                "price_change_pct": divergence.price_change_pct,
                "direction": divergence.direction,
                "prev_oi": prev_oi,
                "curr_oi": curr_oi,
            },
            timestamp=datetime.utcnow(),
        )
        await self._emit_alert(alert)


whale_detector = WhaleDetector()
