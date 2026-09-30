import asyncio
import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.services.auth_service import verify_jwt
from app.utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_CHANNELS = {
    "market_state",
    "heatmap",
    "whale_alerts",
    "oi_alerts",
    "trader_activity",
    "social_feed",
    "trades",
    "copy_updates",
    "sl_tp_triggered",
}


class ClientConnection:
    def __init__(self, websocket: WebSocket, user_id: str | None) -> None:
        self.websocket = websocket
        self.user_id = user_id
        self.subscriptions: dict[str, set[int | None]] = {}
        # channel_name -> set of market_ids (None means all markets)

    def is_subscribed(self, channel: str, market_id: int | None = None) -> bool:
        if channel not in self.subscriptions:
            return False
        market_ids = self.subscriptions[channel]
        return None in market_ids or market_id in market_ids


class ClientManager:
    def __init__(self) -> None:
        self._clients: list[ClientConnection] = []
        self._lock = asyncio.Lock()

    async def add(self, conn: ClientConnection) -> None:
        async with self._lock:
            self._clients.append(conn)
        logger.info(
            "Client connected, user=%s, total=%d",
            conn.user_id,
            len(self._clients),
        )

    async def remove(self, conn: ClientConnection) -> None:
        async with self._lock:
            try:
                self._clients.remove(conn)
            except ValueError:
                pass
        logger.info(
            "Client disconnected, user=%s, total=%d",
            conn.user_id,
            len(self._clients),
        )

    async def broadcast(
        self, channel: str, data: dict, market_id: int | None = None
    ) -> None:
        message = json.dumps(
            {"channel": channel, "market_id": market_id, "data": data}
        )
        async with self._lock:
            clients_snapshot = list(self._clients)

        disconnected = []
        for conn in clients_snapshot:
            if conn.is_subscribed(channel, market_id):
                try:
                    if conn.websocket.client_state == WebSocketState.CONNECTED:
                        await conn.websocket.send_text(message)
                except Exception:
                    disconnected.append(conn)

        for conn in disconnected:
            await self.remove(conn)

    async def send_to_user(
        self, user_id: str, channel: str, data: dict
    ) -> None:
        message = json.dumps({"channel": channel, "data": data})
        async with self._lock:
            clients_snapshot = list(self._clients)

        for conn in clients_snapshot:
            if conn.user_id == user_id and conn.is_subscribed(channel):
                try:
                    if conn.websocket.client_state == WebSocketState.CONNECTED:
                        await conn.websocket.send_text(message)
                except Exception:
                    await self.remove(conn)

    @property
    def connected_count(self) -> int:
        return len(self._clients)


client_manager = ClientManager()


async def client_feed_endpoint(websocket: WebSocket) -> None:
    # Authenticate via token query param
    token = websocket.query_params.get("token")
    user_id: str | None = None

    if token:
        try:
            payload = verify_jwt(token)
            user_id = payload.get("sub")
        except ValueError:
            await websocket.close(code=4001, reason="Invalid token")
            return

    await websocket.accept()
    conn = ClientConnection(websocket, user_id)
    await client_manager.add(conn)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"error": "Invalid JSON"})
                )
                continue

            # Handle subscribe
            if "subscribe" in msg:
                channel = msg["subscribe"]
                if channel not in SUPPORTED_CHANNELS:
                    await websocket.send_text(
                        json.dumps(
                            {"error": f"Unknown channel: {channel}"}
                        )
                    )
                    continue

                # copy_updates requires authentication
                if channel == "copy_updates" and not user_id:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "error": "Authentication required for copy_updates"
                            }
                        )
                    )
                    continue

                market_id = msg.get("market_id")
                if channel not in conn.subscriptions:
                    conn.subscriptions[channel] = set()
                conn.subscriptions[channel].add(market_id)

                await websocket.send_text(
                    json.dumps(
                        {
                            "subscribed": channel,
                            "market_id": market_id,
                        }
                    )
                )
                logger.debug(
                    "Client %s subscribed to %s market_id=%s",
                    user_id,
                    channel,
                    market_id,
                )

            # Handle unsubscribe
            elif "unsubscribe" in msg:
                channel = msg["unsubscribe"]
                market_id = msg.get("market_id")

                if channel in conn.subscriptions:
                    if market_id is not None:
                        conn.subscriptions[channel].discard(market_id)
                        if not conn.subscriptions[channel]:
                            del conn.subscriptions[channel]
                    else:
                        del conn.subscriptions[channel]

                await websocket.send_text(
                    json.dumps(
                        {
                            "unsubscribed": channel,
                            "market_id": market_id,
                        }
                    )
                )
            else:
                await websocket.send_text(
                    json.dumps({"error": "Unknown message format"})
                )

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Client WS error for user=%s", user_id)
    finally:
        await client_manager.remove(conn)
