import json
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from web3 import Web3

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Dedicated pool for the parallel per-market reads inside
# get_trader_positions_only. Sized for 4 markets per leader; small and
# bounded so a burst of callers can't exhaust threads.
_POSITIONS_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="chainpos")

# Live market map: {market_id: {symbol, price_decimals, size_decimals}}.
# Seeded with the current Perpl markets and kept fresh in-place by the registry
# (see apply_registry) so position/orderbook scans follow listings/delistings
# instead of a stale {1,10,20,30}. Mutated IN PLACE so importers see updates.
MARKETS = {
    1: {"symbol": "BTC", "price_decimals": 1, "size_decimals": 5},
    10: {"symbol": "MON", "price_decimals": 6, "size_decimals": 0},
    20: {"symbol": "ETH", "price_decimals": 2, "size_decimals": 3},
    31: {"symbol": "SOL", "price_decimals": 3, "size_decimals": 3},
    40: {"symbol": "HYPE", "price_decimals": 4, "size_decimals": 2},
    50: {"symbol": "ZEC", "price_decimals": 2, "size_decimals": 4},
}


def apply_registry(markets: list) -> None:
    """Refresh MARKETS in place from normalized registry entries. In-place so
    modules that did `from chain_reader import MARKETS` see the update."""
    rebuilt = {}
    for m in markets:
        mid = m.get("market_id")
        pd = m.get("price_decimals")
        sd = m.get("size_decimals")
        if mid is None or pd is None or sd is None:
            continue
        rebuilt[mid] = {
            "symbol": m.get("symbol") or f"MKT-{mid}",
            "price_decimals": pd,
            "size_decimals": sd,
        }
    if rebuilt:
        MARKETS.clear()
        MARKETS.update(rebuilt)


def market_symbol(market_id: int) -> str:
    """Symbol for a market id, or MKT-<id> if unknown."""
    m = MARKETS.get(market_id)
    return m["symbol"] if m else f"MKT-{market_id}"

# Mainnet: 0=long, 1=short (different from testnet)
POS_TYPES = {0: "long", 1: "short"}
ORDER_TYPES = {
    0: "none", 1: "maker_buy", 2: "maker_sell",
    3: "taker_buy", 4: "taker_sell",
    5: "stop_loss", 6: "take_profit",
}

CONTRACT_ADDR = "0x34b6552d57a35a1d042ccae1951bd1c370112a6f"


@lru_cache(maxsize=1)
def _load_abi() -> list:
    abi_path = Path(__file__).parent.parent.parent / "perpl_abi.json"
    with open(abi_path) as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _get_w3_contract():
    # Cached singleton so the underlying requests.Session is reused across
    # calls (keepalive). Without this, every chain read paid a fresh
    # TCP/TLS handshake.
    w3 = Web3(Web3.HTTPProvider(settings.MONAD_RPC_URL))
    abi = _load_abi()
    return w3, w3.eth.contract(
        address=Web3.to_checksum_address(CONTRACT_ADDR), abi=abi
    )


def get_perpl_account_id(wallet_address: str) -> int:
    """Return the on-chain Perpl account id for a wallet (0 = no Perpl account).

    A non-zero id means the wallet is an approved/registered Perpl trading account
    (the SAME signal the terminal/manual path uses before placing an order — see
    `get_trader_positions_only` and the frontend `session.accountId` check). Single
    lightweight RPC call (getAccountByAddr).

    The contract REVERTS (custom error) for a wallet with no account — that's a clean
    'not eligible' (returns 0). Genuine RPC/network errors propagate so the caller can
    fail-closed."""
    from web3.exceptions import ContractLogicError
    w3, contract = _get_w3_contract()
    addr = Web3.to_checksum_address(wallet_address)
    try:
        acct = contract.functions.getAccountByAddr(addr).call()
        return int(acct[0])
    except ContractLogicError:
        return 0  # no Perpl account for this wallet


def is_perpl_eligible(wallet_address: str) -> bool:
    """True if the wallet has a Perpl trading account (account_id != 0)."""
    return get_perpl_account_id(wallet_address) != 0


# --- Market liquidation parameters (Wallet Explorer Part 3) -----------------
# getLiquidationInfo(perpId) is a MARKET-LEVEL view: liquidation fee splits
# (insurance/user/protocol per 100K), back-to-liquidity threshold and its
# splits. It does NOT return a per-position liquidation price — that is
# computed by the documented formula liq = entry ± (deposit − MMR)/size with
# MMR = notional × 100/maintenanceMarginHdths (same as health_dashboard).
_LIQ_INFO_TTL = 3600.0
_liq_info_cache: dict[int, tuple[float, dict]] = {}


def get_liquidation_info(perp_id: int) -> dict | None:
    """Market liquidation parameters from the previously-unused
    getLiquidationInfo view (1h in-process cache)."""
    import time as _time
    now = _time.monotonic()
    c = _liq_info_cache.get(perp_id)
    if c and now - c[0] < _LIQ_INFO_TTL:
        return c[1]
    try:
        w3, contract = _get_w3_contract()
        li = contract.functions.getLiquidationInfo(perp_id).call()
        out = {
            "liq_insurance_per_100k": int(li[0]),
            "liq_user_per_100k": int(li[1]),
            "liq_protocol_per_100k": int(li[2]),
            "btl_price_thresh_per_100k": int(li[3]),
            "btl_insurance_per_100k": int(li[4]),
            "btl_user_per_100k": int(li[5]),
            "btl_buyer_per_100k": int(li[6]),
            "btl_protocol_per_100k": int(li[7]),
            "btl_restrict_buyers": bool(li[8]),
        }
        _liq_info_cache[perp_id] = (now, out)
        return out
    except Exception as exc:
        logger.warning("getLiquidationInfo failed for perp %s: %s", perp_id, exc)
        return None


def liq_price_for(position: dict, maintenance_margin_hdths: int) -> float | None:
    """Per-position liquidation price by the documented formula (CLAUDE.md /
    health_dashboard): liq = entry ± (deposit − MMR)/size, MMR = notional ×
    (100/maintenanceMarginHdths). None when inputs are missing — never
    invented."""
    try:
        size = float(position["size"])
        entry = float(position["entry_price"])
        deposit = float(position["deposit"])
        mark = float(position.get("mark_price") or 0)
        if size <= 0 or entry <= 0 or deposit <= 0 or maintenance_margin_hdths <= 0:
            return None
        notional = size * (mark or entry)
        mmr_usd = notional * (100.0 / maintenance_margin_hdths)
        if position["side"] == "long":
            return max(0.0, entry - (deposit - mmr_usd) / size)
        if position["side"] == "short":
            return entry + (deposit - mmr_usd) / size
        return None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def get_trader_positions_only(wallet_address: str) -> dict | None:
    """Lightweight: account + positions only. Skips pending orders (~half the
    RPC calls of get_trader_detail) and runs the 4 per-market getPosition
    reads in parallel via a small thread pool.

    Used by the Following feed and the Dashboard 'Live from Leaders' widget,
    where pending orders aren't displayed."""
    try:
        w3, contract = _get_w3_contract()
        addr = Web3.to_checksum_address(wallet_address)
        acct = contract.functions.getAccountByAddr(addr).call()
        account_id = acct[0]

        if account_id == 0:
            return None

        balance = acct[1] / 1e6
        margin_used = acct[2] / 1e6

        def _read_position(perp_id: int, mcfg: dict) -> dict | None:
            try:
                pos = contract.functions.getPosition(perp_id, account_id).call()
                p = pos[0]
                if p[4] <= 0:
                    return None
                pd = 10 ** mcfg["price_decimals"]
                sd = 10 ** mcfg["size_decimals"]
                entry_price = p[5] / pd
                size = p[6] / sd
                mark_price = pos[1] / pd
                deposit = p[4] / 1e6
                pnl = p[8] / 1e6
                leverage = (size * entry_price / deposit) if deposit > 0 else 0
                return {
                    "market_id": perp_id,
                    "symbol": mcfg["symbol"],
                    "side": POS_TYPES.get(p[3], "unknown"),
                    "size": size,
                    "entry_price": round(entry_price, mcfg["price_decimals"]),
                    "mark_price": round(mark_price, mcfg["price_decimals"]),
                    "pnl": round(pnl, 2),
                    "delta_pnl": round(p[9] / 1e6, 2),
                    "funding_pnl": round(p[10] / 1e6, 2),
                    "deposit": round(deposit, 2),
                    "leverage": round(leverage, 1),
                    "notional": round(size * mark_price, 2),
                }
            except Exception:
                return None

        futures = [
            _POSITIONS_POOL.submit(_read_position, perp_id, mcfg)
            for perp_id, mcfg in MARKETS.items()
        ]
        positions = [f.result() for f in futures]
        positions = [p for p in positions if p is not None]

        return {
            "wallet_address": wallet_address,
            "account_id": account_id,
            "balance": round(balance, 2),
            "margin_used": round(margin_used, 2),
            "positions": positions,
            "position_count": len(positions),
        }
    except Exception as e:
        logger.error("Failed to get trader positions for %s: %s", wallet_address, e)
        return None


def get_trader_detail(wallet_address: str) -> dict | None:
    """Get trader's live on-chain state: account + positions + pending orders."""
    try:
        w3, contract = _get_w3_contract()
        addr = Web3.to_checksum_address(wallet_address)
        acct = contract.functions.getAccountByAddr(addr).call()
        account_id = acct[0]

        if account_id == 0:
            return None

        balance = acct[1] / 1e6
        margin_used = acct[2] / 1e6

        positions = []
        orders = []

        for perp_id, mcfg in MARKETS.items():
            pd = 10 ** mcfg["price_decimals"]
            sd = 10 ** mcfg["size_decimals"]

            # Open position — check deposit > 0 (not positionType, which is 0=long on mainnet)
            try:
                pos = contract.functions.getPosition(perp_id, account_id).call()
                p = pos[0]
                if p[4] > 0:  # depositCNS > 0 means position exists
                    entry_price = p[5] / pd
                    size = p[6] / sd
                    mark_price = pos[1] / pd
                    deposit = p[4] / 1e6
                    pnl = p[8] / 1e6
                    leverage = (size * entry_price / deposit) if deposit > 0 else 0

                    positions.append({
                        "market_id": perp_id,
                        "symbol": mcfg["symbol"],
                        "side": POS_TYPES.get(p[3], "unknown"),
                        "size": size,
                        "entry_price": round(entry_price, mcfg["price_decimals"]),
                        "mark_price": round(mark_price, mcfg["price_decimals"]),
                        "pnl": round(pnl, 2),
                        "delta_pnl": round(p[9] / 1e6, 2),
                        "funding_pnl": round(p[10] / 1e6, 2),
                        "deposit": round(deposit, 2),
                        "leverage": round(leverage, 1),
                        "notional": round(size * mark_price, 2),
                    })
            except Exception:
                pass

            # Pending orders.
            # The OrderLock id is a packed value: high 16 bits = perpId,
            # low 16 bits = orderId. Decode it and fetch the full order via
            # getOrder(perpId, orderId) to enrich with price, lot, leverage.
            try:
                plocks = contract.functions.getPerpOrderLocks(account_id, perp_id).call()
                for pl in plocks:
                    lock_id = pl[0]
                    lock_otype = pl[3]
                    amount = pl[5] / 1e6
                    if amount <= 0 or lock_id == 0:
                        continue

                    decoded_perp = (lock_id >> 16) & 0xFFFF
                    order_id = lock_id & 0xFFFF
                    # Sanity: a lock attached via getPerpOrderLocks(account, perp_id)
                    # should decode to the same perp_id. If not, it's a stray.
                    if decoded_perp != perp_id:
                        continue

                    price: float | None = None
                    size: float | None = None
                    leverage: float | None = None
                    expiry_block: int | None = None
                    real_otype = lock_otype
                    try:
                        order = contract.functions.getOrder(perp_id, order_id).call()
                        # order: (accountId, orderType, priceONS, lotLNS,
                        #         recycleFeeRaw, expiryBlock, leverageHdths, ...)
                        if order[0] == account_id:
                            real_otype = order[1] or lock_otype
                            price = order[2] / pd
                            size = order[3] / sd
                            leverage = order[6] / 100 if order[6] else None
                            expiry_block = order[5] or None
                    except Exception:
                        pass

                    side = (
                        "buy" if real_otype in (1, 3)
                        else "sell" if real_otype in (2, 4)
                        else "trigger" if real_otype in (5, 6)
                        else "unknown"
                    )
                    orders.append({
                        "market_id": perp_id,
                        "symbol": mcfg["symbol"],
                        "order_type": ORDER_TYPES.get(real_otype, f"type_{real_otype}"),
                        "is_maker": real_otype in (1, 2),
                        "is_taker": real_otype in (3, 4),
                        "is_trigger": real_otype in (5, 6),
                        "side": side,
                        "order_id": order_id,
                        "lock_id": lock_id,
                        "price": round(price, mcfg["price_decimals"]) if price else None,
                        "size": round(size, mcfg["size_decimals"]) if size else None,
                        "leverage": round(leverage, 1) if leverage else None,
                        "notional": round(price * size, 2) if (price and size) else None,
                        "margin_locked": round(amount, 2),
                        "expiry_block": expiry_block,
                    })
            except Exception:
                pass

        return {
            "wallet_address": wallet_address,
            "account_id": account_id,
            "balance": round(balance, 2),
            "margin_used": round(margin_used, 2),
            "positions": positions,
            "orders": orders,
            "position_count": len(positions),
            "order_count": len(orders),
        }
    except Exception as e:
        logger.error("Failed to get trader detail for %s: %s", wallet_address, e)
        return None
