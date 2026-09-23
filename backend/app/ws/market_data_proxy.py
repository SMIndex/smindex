import asyncio
import json

import websockets
from websockets.exceptions import ConnectionClosed
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

PERPL_MARKET_DATA_WS = f"{settings.PERPL_WS_URL}/market-data"


async def market_data_proxy_endpoint(client_ws: WebSocket) -> None:
    """Proxy WebSocket between browser and Perpl market-data WS.

    Public data — no authentication needed.
    Client subscribes to order-book@{marketId} and trades@{marketId}.
    """
    await client_ws.accept()
    perpl_ws = None

    try:
        perpl_ws = await asyncio.wait_for(
            websockets.connect(
                PERPL_MARKET_DATA_WS,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ),
            timeout=10,
        )
        logger.info("Market data proxy: connected to %s", PERPL_MARKET_DATA_WS)

        async def client_to_perpl():
            try:
                while True:
                    data = await client_ws.receive_text()
                    await perpl_ws.send(data)
            except WebSocketDisconnect:
                pass
            except Exception:
                pass

        async def perpl_to_client():
            try:
                async for msg in perpl_ws:
                    text = msg if isinstance(msg, str) else msg.decode()
                    if client_ws.client_state == WebSocketState.CONNECTED:
                        await client_ws.send_text(text)
            except ConnectionClosed as e:
                logger.info("Market data proxy: Perpl closed %s: %s", e.code, e.reason)
                try:
                    if client_ws.client_state == WebSocketState.CONNECTED:
                        await client_ws.send_text(json.dumps({"error": f"Perpl: {e.reason}", "code": e.code}))
                except Exception:
                    pass
            except Exception:
                pass

        done, pending = await asyncio.wait(
            [
                asyncio.create_task(client_to_perpl()),
                asyncio.create_task(perpl_to_client()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    except asyncio.TimeoutError:
        logger.error("Market data proxy: timeout connecting to Perpl")
        try:
            await client_ws.send_text(json.dumps({"error": "Connection timeout"}))
        except Exception:
            pass
    except Exception as e:
        logger.error("Market data proxy error: %s", e)
    finally:
        if perpl_ws:
            try:
                await perpl_ws.close()
            except Exception:
                pass
        logger.info("Market data proxy: disconnected")
