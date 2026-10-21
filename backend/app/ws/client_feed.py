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


AUTH_WAIT_S = 5.0


def _auth_frame_token(msg: Any) -> tuple[bool, str | None]:
    """(is an auth frame, token). The page's first frame is {"type": "auth", "token": t},
    with token null for a guest, so the session token never travels in the URL."""
    if isinstance(msg, dict) and msg.get("type") == "auth":
        t = msg.get("token")
        return True, t if isinstance(t, str) and t else None
    return False, None


async def client_feed_endpoint(websocket: WebSocket) -> None:
    user_id: str | None = None
    # TODO(2026-10-28): remove this legacy ?token= path and the no-auth-frame
    # fallback below. They exist only for tabs opened before 2026-10-21.
    token = websocket.query_params.get("token")
    if token:
        try:
            payload = verify_jwt(token)
            user_id = payload.get("sub")
        except ValueError:
            await websocket.close(code=4001, reason="Invalid token")
            return

    await websocket.accept()
    pending: str | None = None
    if not token:
        # Wait up to 5 s for the auth frame; a socket that says nothing is closed.
        try:
            first = await asyncio.wait_for(websocket.receive_text(), AUTH_WAIT_S)
        except asyncio.TimeoutError:
            await websocket.close(code=4401, reason="Auth frame required")
            return
        except WebSocketDisconnect:
            return
        try:
            msg = json.loads(first)
        except json.JSONDecodeError:
            msg = None
        is_auth, frame_token = _auth_frame_token(msg)
        if is_auth:
            if frame_token:
                try:
                    user_id = verify_jwt(frame_token).get("sub")
                except ValueError:
                    await websocket.close(code=4001, reason="Invalid token")
                    return
            await websocket.send_text(json.dumps({"authenticated": bool(user_id)}))
        else:
            # Legacy guest tab (before 2026-10-21): its first frame is a subscribe.
            pending = first

    conn = ClientConnection(websocket, user_id)
    await client_manager.add(conn)

    try:
        while True:
            if pending is not None:
                raw, pending = pending, None
            else:
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
