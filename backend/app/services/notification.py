import asyncio
from typing import Any, Callable, Coroutine

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Event type constants
EVENT_LEADER_TRADE = "leader_trade"
EVENT_WHALE_ALERT = "whale_alert"
EVENT_MARKET_STATE_UPDATE = "market_state_update"
EVENT_HEATMAP_UPDATE = "heatmap_update"
EVENT_COPY_EXECUTION = "copy_execution"


class EventBus:
    _instance: "EventBus | None" = None

    def __new__(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._subscribers = {}
        return cls._instance

    def subscribe(
        self,
        event_type: str,
        callback: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)
            logger.debug("Subscribed %s to %s", callback.__name__, event_type)

    def unsubscribe(
        self,
        event_type: str,
        callback: Callable[..., Coroutine[Any, Any, None]],
    ) -> None:
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(callback)
                logger.debug(
                    "Unsubscribed %s from %s", callback.__name__, event_type
                )
            except ValueError:
                pass

    async def emit(self, event_type: str, data: Any) -> None:
        subscribers = self._subscribers.get(event_type, [])
        if not subscribers:
            return

        logger.debug(
            "Emitting %s to %d subscribers", event_type, len(subscribers)
        )

        tasks = []
        for callback in subscribers:
            tasks.append(self._safe_call(callback, event_type, data))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_call(
        self,
        callback: Callable[..., Coroutine[Any, Any, None]],
        event_type: str,
        data: Any,
    ) -> None:
        try:
            await callback(data)
        except Exception:
            logger.exception(
                "Error in event handler %s for %s",
                callback.__name__,
                event_type,
            )


event_bus = EventBus()
