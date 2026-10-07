import asyncio
import json
import uuid

import websockets
from websockets.exceptions import ConnectionClosed
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

PERPL_TRADING_WS = f"{settings.PERPL_WS_URL}/trading"


async def trading_proxy_endpoint(client_ws: WebSocket) -> None:
    """Proxy WebSocket between browser and Perpl trading WS.

    The client sends {mt:4, nonce:..., chain_id:...} as first message.
    We intercept it, add cookies from the stored Perpl session, and
    connect to Perpl's trading WS with proper headers.
    """
    await client_ws.accept()
    perpl_ws = None

    try:
        # Wait for the auth message from the client.
        # mt:4  = legacy SIWE-session auth (needs stored Perpl cookies)
        # mt:29 = Ed25519 API-key sign-in (self-contained, forwarded verbatim)
        raw_auth = await asyncio.wait_for(client_ws.receive_text(), timeout=10)
        auth_msg = json.loads(raw_auth)
        is_apikey_auth = auth_msg.get("mt") == 29
        nonce = auth_msg.get("nonce", "")
        client_address = (auth_msg.get("address") or "").lower()
        token = auth_msg.get("token", "")
        session_id = str(uuid.uuid4())

        # AUTHENTICATE: the trusted wallet comes ONLY from the verified app JWT, never
        # from the client-supplied `address`. Without this, a user could supply another
        # wallet's address and load that wallet's stored Perpl cookies -> trade as them
        # (account takeover). Never log the token or cookies.
        from app.services.auth_service import verify_jwt
        trusted_wallet = ""
        try:
            if token:
                payload = verify_jwt(token)
                trusted_wallet = (payload.get("wallet") or "").lower()
        except Exception:
            trusted_wallet = ""

        if not trusted_wallet:
            logger.warning("Trading proxy: rejected connection — missing/invalid auth token")
            try:
                await client_ws.send_text(json.dumps({"error": "Authentication required", "code": 4401}))
            except Exception:
                pass
            await client_ws.close(code=4401)
            return

        # Reject any attempt to trade under a wallet other than the authenticated one.
        if client_address and client_address != trusted_wallet:
            logger.warning(
                "Trading proxy: rejected wallet mismatch (auth=%s… requested=%s…)",
                trusted_wallet[:8], client_address[:8],
            )
            try:
                await client_ws.send_text(json.dumps({"error": "Wallet does not match your session", "code": 4403}))
            except Exception:
                pass
            await client_ws.close(code=4403)
            return

        logger.info("Trading proxy: authenticated wallet=%s…", trusted_wallet[:8])

        extra_headers = {"x-browser-session-id": session_id}

        if not is_apikey_auth:
            # Legacy flow: load stored Perpl cookies ONLY for the authenticated wallet.
            from app.routers.auth import get_perpl_session, get_perpl_session_async
            perpl_session = get_perpl_session(trusted_wallet)
            if not perpl_session:
                perpl_session = await get_perpl_session_async(trusted_wallet)
            cookies = perpl_session.get("cookies", {}) if perpl_session else {}
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items()) if cookies else ""
            if cookie_str:
                extra_headers["Cookie"] = cookie_str

        perpl_ws = await asyncio.wait_for(
            websockets.connect(
                PERPL_TRADING_WS,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                extra_headers=extra_headers,
            ),
            timeout=10,
        )
        logger.info("Trading proxy: connected to Perpl WS (auth=%s)", "apikey" if is_apikey_auth else "session")

        if is_apikey_auth:
            # Forward the signed mt:29 frame verbatim, minus our proxy-only fields.
            # The Ed25519 signature covers chain_id/timestamp/nonce — we cannot and
            # must not alter those.
            signin = {k: v for k, v in auth_msg.items() if k not in ("token", "address")}
            await perpl_ws.send(json.dumps(signin))
        else:
            auth_to_perpl = {
                "mt": 4,
                "nonce": nonce,
                "chain_id": auth_msg.get("chain_id", 143),
                "ses": session_id,
            }
            await perpl_ws.send(json.dumps(auth_to_perpl))
        logger.info("Trading proxy: sent auth to Perpl")

        async def client_to_perpl():
            try:
                while True:
                    data = await client_ws.receive_text()
                    # Do not log full order payloads (order details / nonces).
                    logger.debug("Trading proxy client->perpl (%d bytes)", len(data))
                    await perpl_ws.send(data)
            except WebSocketDisconnect:
                pass
            except Exception:
                pass

        async def perpl_to_client():
            try:
                async for msg in perpl_ws:
                    text = msg if isinstance(msg, str) else msg.decode()
                    logger.debug("Trading proxy perpl->client (%d bytes)", len(text))
                    if client_ws.client_state == WebSocketState.CONNECTED:
                        await client_ws.send_text(text)
            except ConnectionClosed as e:
                logger.info("Trading proxy: Perpl closed %s: %s", e.code, e.reason)
                error_msg = json.dumps({"error": f"Perpl: {e.reason}", "code": e.code})
                try:
                    if client_ws.client_state == WebSocketState.CONNECTED:
                        await client_ws.send_text(error_msg)
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
        logger.error("Trading proxy: timeout")
        try:
            await client_ws.send_text(json.dumps({"error": "Connection timeout"}))
        except Exception:
            pass
    except Exception as e:
        logger.error("Trading proxy error: %s", e)
        try:
            await client_ws.send_text(json.dumps({"error": str(e)}))
        except Exception:
            pass
    finally:
        if perpl_ws:
            try:
                await perpl_ws.close()
            except Exception:
                pass
        logger.info("Trading proxy: session ended")
