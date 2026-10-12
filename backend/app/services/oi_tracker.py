"""Tracks Open Interest over sliding windows and detects spikes.

Emits oi_spike alerts when OI changes > threshold within the window.
Market-specific thresholds: BTC needs smaller % (large OI), MON needs larger %.
"""
import time
from collections import deque
from datetime import datetime

from app.services.notification import event_bus
from app.utils.logger import get_logger

logger = get_logger(__name__)

EVENT_OI_SPIKE = "oi_spike"

# Market-specific thresholds (% change in window)
OI_THRESHOLDS = {
    1: 0.03,   # BTC: 3% OI change
    10: 0.08,  # MON: 8% OI change (smaller OI, more volatile)
    20: 0.05,  # ETH: 5% OI change
}
DEFAULT_THRESHOLD = 0.05
WINDOW_SECONDS = 300  # 5 minutes


class OITracker:
    def __init__(self):
        # market_id -> deque of (timestamp, oi_usd)
        self._history: dict[int, deque] = {}
        self._last_alert_time: dict[int, float] = {}
        self._cooldown = 60  # min seconds between alerts per market

    async def record(self, market_id: int, oi_usd: float):
        """Record an OI reading and check for spikes."""
        now = time.time()

        if market_id not in self._history:
            self._history[market_id] = deque(maxlen=200)

        self._history[market_id].append((now, oi_usd))

        # Trim old entries
        cutoff = now - WINDOW_SECONDS
        while self._history[market_id] and self._history[market_id][0][0] < cutoff:
            self._history[market_id].popleft()

        # Need at least 2 entries to compare
        if len(self._history[market_id]) < 2:
            return

        oldest = self._history[market_id][0]
        change_pct = (oi_usd - oldest[1]) / oldest[1] if oldest[1] > 0 else 0
        threshold = OI_THRESHOLDS.get(market_id, DEFAULT_THRESHOLD)

        if abs(change_pct) >= threshold:
            # Cooldown check
            last_alert = self._last_alert_time.get(market_id, 0)
            if now - last_alert < self._cooldown:
                return

            self._last_alert_time[market_id] = now
            direction = "spike_up" if change_pct > 0 else "spike_down"
            change_usd = oi_usd - oldest[1]

            alert_data = {
                "market_id": market_id,
                "alert_type": "oi_spike",
                "direction": direction,
                "change_pct": round(change_pct * 100, 2),
                "change_usd": round(change_usd, 2),
                "current_oi": round(oi_usd, 2),
                "window_seconds": WINDOW_SECONDS,
                "timestamp": datetime.utcnow().isoformat(),
            }

            logger.info(
                "OI spike detected: market=%d direction=%s change=%.2f%% ($%.2f)",
                market_id, direction, change_pct * 100, change_usd,
            )

            await event_bus.emit(EVENT_OI_SPIKE, alert_data)

            # Broadcast to WS clients
            from app.ws.client_feed import client_manager
            await client_manager.broadcast("oi_alerts", alert_data, market_id=market_id)


oi_tracker = OITracker()
