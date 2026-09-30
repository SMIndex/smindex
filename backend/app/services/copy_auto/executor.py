"""Live execution for auto-copy: one authenticated Perpl trading WS per user,
opened on demand, reconnected once on failure. The ONLY module that decrypts
a user's key (keys.load_secret). Market parameters come from the same sources
the browser uses (/api/market-configs): decimals + min size from the market
registry, orderTtlBlocks + max market slippage from the Perpl context."""
import asyncio
import time

from app.services.copy_auto import keys
from app.services.copy_auto.perpl_ws import OrderRejected, PerplTrader
from app.utils.logger import get_logger

logger = get_logger(__name__)

_traders: dict[int, PerplTrader] = {}
_ctx_cache: tuple[float, dict] = (0.0, {})


class KeyRejected(Exception):
    pass


async def market_params(market_id: int) -> dict | None:
    """price/size decimals, min scaled size, ttl blocks, slippage cap (bps)."""
    global _ctx_cache
    from app.services import market_registry
    from app.services.perpl_client import perpl_client
    m = await market_registry.get_market_by_id(market_id)
    if not m or not m.get("is_active"):
        return None
    if time.monotonic() - _ctx_cache[0] > 60:
        ctx = await perpl_client.get_context()
        _ctx_cache = (time.monotonic(), {x.get("id"): x for x in ctx.get("markets", [])})
    raw = _ctx_cache[1].get(market_id, {})
    return {"price_decimals": int(m["price_decimals"]), "size_decimals": int(m["size_decimals"]),
            "min_scaled": int(m.get("min_order_size") or 0),
            "ttl_blocks": int(raw.get("order_ttl_blocks", 6) or 6),
            "slippage_bps": int(raw.get("order_max_market_slippage_bps", 100) or 100)}


async def _trader(user_id: int, wallet: str) -> PerplTrader:
    t = _traders.get(user_id)
    if t and t.ws is not None and t._reader and not t._reader.done():
        return t
    secret = await keys.load_secret(user_id)
    if secret is None:
        raise KeyRejected("no Perpl key deposited")
    from app.services import chain_reader
    detail = await asyncio.to_thread(chain_reader.get_trader_positions_only, wallet)
    acct = (detail or {}).get("account_id")
    if not acct:
        raise KeyRejected("no on-chain Perpl account for this wallet")
    t = PerplTrader(secret[0], secret[1], acct)
    try:
        await t.connect()
    except OrderRejected as exc:
        await keys.mark(user_id, error=str(exc))
        raise KeyRejected(str(exc))
    _traders[user_id] = t
    return t


async def _call(user_id: int, wallet: str, fn_name: str, **kw):
    for attempt in (1, 2):
        t = await _trader(user_id, wallet)
        try:
            r = await getattr(t, fn_name)(**kw)
            await keys.mark(user_id, used=True)
            return r
        except (ConnectionError, OSError) as exc:
            logger.warning("auto-copy WS %s failed (attempt %d) for user %s: %s", fn_name, attempt, user_id, exc)
            await t.close()
            _traders.pop(user_id, None)
            if attempt == 2:
                raise


async def open_market(user_id, wallet, *, market_id, side, size, leverage, mark, mp) -> dict:
    return await _call(user_id, wallet, "market_open", market_id=market_id, side=side, size=size,
                       leverage=leverage, mark=mark, size_decimals=mp["size_decimals"],
                       price_decimals=mp["price_decimals"], slippage_bps=mp["slippage_bps"],
                       ttl_blocks=mp["ttl_blocks"])


async def close_market(user_id, wallet, *, market_id, side, size, mark, mp) -> dict:
    return await _call(user_id, wallet, "market_close", market_id=market_id, side=side, size=size,
                       mark=mark, size_decimals=mp["size_decimals"], price_decimals=mp["price_decimals"],
                       slippage_bps=mp["slippage_bps"], ttl_blocks=mp["ttl_blocks"])


async def stop_loss(user_id, wallet, *, market_id, side, size, trigger, mp) -> dict:
    return await _call(user_id, wallet, "stop_loss", market_id=market_id, side=side, size=size,
                       trigger=trigger, size_decimals=mp["size_decimals"], price_decimals=mp["price_decimals"])


async def cancel(user_id, wallet, *, market_id, oid) -> dict:
    return await _call(user_id, wallet, "cancel", market_id=market_id, oid=oid)


async def shutdown() -> None:
    for t in list(_traders.values()):
        await t.close()
    _traders.clear()
