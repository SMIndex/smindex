"""Hyperliquid live mid prices + funding — cross-venue basis source.

One outbound ws to wss://api.hyperliquid.xyz/ws, subscription `allMids`
(market-wide, NOT a user subscription — does not consume the 10-user budget).
Maintains `hl_price_cache: {COIN: mid}` updated per push. Reconnects with
exponential backoff (same discipline as the existing Perpl proxies). A 60s
`metaAndAssetCtxs` REST poll fills `hl_funding_cache: {COIN: hourly_rate}`.

Basis for a mapped market = (hl_mid - perpl_mark) / perpl_mark * 10_000 bps,
with perpl_mark read from ws_manager.market_state_cache by the caller.
"""
import asyncio
import json
import time

import websockets

from app.utils.logger import get_logger

logger = get_logger(__name__)

WS_URL = "wss://api.hyperliquid.xyz/ws"
FUNDING_POLL_SEC = 60

hl_price_cache: dict[str, float] = {}
hl_funding_cache: dict[str, float] = {}
_last_mid_at: float = 0.0

_ws_task: asyncio.Task | None = None
_funding_task: asyncio.Task | None = None


def get_mid(coin: str) -> float | None:
    return hl_price_cache.get(coin.upper())


def get_funding(coin: str) -> float | None:
    return hl_funding_cache.get(coin.upper())


def stream_age_sec() -> float | None:
    return (time.monotonic() - _last_mid_at) if _last_mid_at else None


# perpl_market_id -> HL coin (from market_map, 60s TTL)
_map_cache: tuple[float, dict[int, str]] | None = None


async def _coin_for_market(perpl_market_id: int) -> str | None:
    global _map_cache
    now = time.monotonic()
    if _map_cache is None or now - _map_cache[0] > 60:
        from sqlalchemy import select
        from app.db.database import get_session_factory
        from app.db.copy_models import MarketMap
        sf = get_session_factory()
        async with sf() as session:
            rows = (await session.execute(
                select(MarketMap).where(MarketMap.exchange == "hl",
                                        MarketMap.perpl_market_id.isnot(None))
            )).scalars().all()
        _map_cache = (now, {r.perpl_market_id: r.native_symbol.upper() for r in rows})
    return _map_cache[1].get(perpl_market_id)


async def basis_bps_for_market(perpl_market_id: int) -> float | None:
    """(HL mid - Perpl mark) / Perpl mark in bps, or None when either side is
    unavailable (no mapped coin, stream not primed, Perpl state missing)."""
    coin = await _coin_for_market(perpl_market_id)
    if not coin:
        return None
    hl_mid = get_mid(coin)
    if not hl_mid:
        return None
    from app.services.ws_manager import ws_manager
    perpl_mark = None
    if ws_manager:
        perpl_mark = (ws_manager.market_state_cache.get(perpl_market_id) or {}).get("mark_price")
    if not perpl_mark:
        return None
    return (hl_mid - perpl_mark) / perpl_mark * 10_000


async def _ws_loop() -> None:
    global _last_mid_at
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10,
                                          close_timeout=5) as ws:
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "allMids"},
                }))
                logger.info("HL allMids ws connected")
                backoff = 1.0
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("channel") == "allMids":
                        mids = (msg.get("data") or {}).get("mids") or {}
                        for coin, mid in mids.items():
                            try:
                                hl_price_cache[coin.upper()] = float(mid)
                            except (TypeError, ValueError):
                                continue
                        _last_mid_at = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("HL allMids ws dropped (%s) — reconnecting in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def _funding_loop() -> None:
    while True:
        try:
            from app.services.hyperliquid import client as hl_client
            resp = await hl_client.post_info({"type": "metaAndAssetCtxs"},
                                             priority=hl_client.BACKGROUND,
                                             timeout=15.0)
            resp.raise_for_status()
            meta, ctxs = resp.json()
            universe = meta.get("universe", [])
            for asset, ctx in zip(universe, ctxs):
                coin = str(asset.get("name", "")).upper()
                try:
                    hl_funding_cache[coin] = float(ctx.get("funding") or 0)
                except (TypeError, ValueError):
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("HL funding poll failed: %s", exc)
        await asyncio.sleep(FUNDING_POLL_SEC)


def start() -> None:
    global _ws_task, _funding_task
    loop = asyncio.get_event_loop()
    if _ws_task is None or _ws_task.done():
        _ws_task = loop.create_task(_ws_loop())
    if _funding_task is None or _funding_task.done():
        _funding_task = loop.create_task(_funding_loop())
    logger.info("HL prices service started (allMids ws + %ss funding poll)", FUNDING_POLL_SEC)


async def stop() -> None:
    global _ws_task, _funding_task
    for t in (_ws_task, _funding_task):
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
    _ws_task = _funding_task = None
