"""Cached live active-position summary for traders (Discover / Watchlist cards).

Reads each trader's REAL on-chain Perpl positions (same contract the trader-profile
positions endpoint uses) and summarizes count + per-market side/size/leverage/pnl.

Markets + decimals + symbols come from the DYNAMIC market registry (never the
hardcoded chain_reader.MARKETS) — so a delisted market (SOL) is not probed and a new
one (HYPE) is included automatically. Paper/copy tables are never touched.

Performance: per-wallet result cached (short TTL); on-chain reads run in a thread with
bounded concurrency + a per-wallet timeout, and any failure degrades to an error flag
(active_positions_count=None) instead of breaking the page.
"""
import asyncio
import time
from functools import lru_cache

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from web3 import Web3
from web3.exceptions import ContractLogicError

from app.config import settings
from app.services import chain_reader, market_registry
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TTL = 45.0          # success cache TTL
_ERR_TTL = 8.0       # cache errors briefly so they self-heal on the next load
_TIMEOUT = 9.0       # per-wallet on-chain budget
_MAX_CONCURRENCY = 6

_cache: dict[str, dict] = {}   # wallet -> {"data": {...}, "ts": monotonic, "ttl"}
_inflight: set[str] = set()    # wallets currently being computed (dedupe priming)
_sema = asyncio.Semaphore(_MAX_CONCURRENCY)

async def _enrich_opened_at(wallet: str, actives: list[dict]) -> None:
    """Fill opened_at (unix seconds) from the trader_tracker's recorded entry events.

    The on-chain PositionInfo.entryBlock is NOT the open time — the contract updates
    it whenever the position is touched (verified live: it moved forward on an open
    position). The tracker's trader_activity table records real entry/exit detections
    (30s polling), so: opened_at = latest 'entry' for (wallet, market, side) with no
    'exit' after it. Untracked wallets / pre-tracking positions stay None ("—")."""
    if not actives:
        return
    try:
        from datetime import timezone
        from sqlalchemy import select
        from app.db.database import get_session_factory
        from app.db.models import TraderActivity

        sf = get_session_factory()
        async with sf() as session:
            rows = (await session.execute(
                select(TraderActivity.market_id, TraderActivity.activity_type,
                       TraderActivity.side, TraderActivity.timestamp)
                .where(TraderActivity.wallet_address == wallet)
                .order_by(TraderActivity.timestamp.desc())
                .limit(400)
            )).all()
        last_entry: dict[int, tuple] = {}   # market_id -> (ts, side)
        last_exit: dict[int, object] = {}   # market_id -> ts
        for mid, atype, side, ts in rows:
            if atype == "entry" and mid not in last_entry:
                last_entry[mid] = (ts, side)
            elif atype == "exit" and mid not in last_exit:
                last_exit[mid] = ts
        for p in actives:
            hit = last_entry.get(p["market_id"])
            if not hit:
                continue
            ts, side = hit
            ex = last_exit.get(p["market_id"])
            if ex is not None and ex >= ts:
                continue                    # position was closed after that entry
            if side and p.get("side") and side != p["side"]:
                continue                    # tracker missed a side flip — don't lie
            p["opened_at"] = int(ts.replace(tzinfo=timezone.utc).timestamp())
    except Exception as e:
        logger.warning("opened_at enrich failed for %s: %s", wallet[:10], e)

POS_LONG, POS_SHORT = 0, 1


@lru_cache(maxsize=1)
def _contract():
    """Dedicated web3 contract with a connection pool + retries sized to our
    concurrency — isolates this batch load from chain_reader's shared session so the
    RPC node isn't hammered (which was dropping reads under burst)."""
    sess = requests.Session()
    retry = Retry(total=2, connect=2, read=2, backoff_factor=0.3,
                  status_forcelist=[429, 500, 502, 503, 504], allowed_methods=None)
    adapter = HTTPAdapter(pool_connections=_MAX_CONCURRENCY + 2,
                          pool_maxsize=_MAX_CONCURRENCY + 2, max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    w3 = Web3(Web3.HTTPProvider(settings.MONAD_RPC_URL, session=sess,
                                request_kwargs={"timeout": 6}))
    abi = chain_reader._load_abi()
    return w3.eth.contract(
        address=Web3.to_checksum_address(chain_reader.CONTRACT_ADDR), abi=abi)


def _account_id(contract, wallet: str) -> int:
    """On-chain Perpl account id (0 = no account; contract reverts for none)."""
    addr = Web3.to_checksum_address(wallet)
    try:
        return int(contract.functions.getAccountByAddr(addr).call()[0])
    except ContractLogicError:
        return 0


def _error_summary() -> dict:
    return {"active_positions_count": None, "active_markets": [],
            "has_active_positions": False, "active_positions_error": True}


def _compute_sync(wallet: str, markets: list[dict]) -> dict:
    """BLOCKING on-chain read. account + getPosition per ACTIVE registry market."""
    contract = _contract()
    account_id = _account_id(contract, wallet)   # revert -> 0 (no account)
    if account_id == 0:
        return {"active_positions_count": 0, "active_markets": [], "has_active_positions": False}

    actives: list[dict] = []
    for m in markets:
        pid = m["market_id"]
        pdv = m.get("price_decimals")
        sdv = m.get("size_decimals")
        if pdv is None or sdv is None:
            continue
        try:
            pos = contract.functions.getPosition(pid, account_id).call()
            p = pos[0]
            if p[4] <= 0:           # deposit == 0 -> no open position in this market
                continue
            pd = 10 ** pdv
            sd = 10 ** sdv
            entry_price = p[5] / pd
            size = p[6] / sd
            mark_price = pos[1] / pd
            deposit = p[4] / 1e6
            pnl = p[8] / 1e6
            leverage = (size * entry_price / deposit) if deposit > 0 else 0
            actives.append({
                "market_id": pid,
                "symbol": m.get("symbol") or f"MKT-{pid}",
                "side": "long" if p[3] == POS_LONG else "short",
                "size": round(size, sdv if sdv and sdv > 0 else 4),
                "leverage": round(leverage, 1),
                "entry_price": round(entry_price, pdv),
                "mark_price": round(mark_price, pdv),
                "unrealized_pnl": round(pnl, 2),
                "pnl": round(pnl, 2),               # alias for profile/copy consumers
                "deposit": round(deposit, 2),
                "notional": round(size * mark_price, 2),
                "opened_at": None,                  # filled by _enrich_opened_at
            })
        except Exception:
            # one bad market read shouldn't drop the whole trader
            continue
    return {"active_positions_count": len(actives), "active_markets": actives,
            "has_active_positions": len(actives) > 0}


async def get_summary(wallet: str) -> dict:
    wallet = (wallet or "").lower()
    if not wallet:
        return _error_summary()
    now = time.monotonic()
    c = _cache.get(wallet)
    if c and now - c["ts"] < c["ttl"]:
        return c["data"]

    async with _sema:
        c = _cache.get(wallet)
        if c and time.monotonic() - c["ts"] < c["ttl"]:   # filled while we waited
            return c["data"]
        try:
            markets = await market_registry.get_active_markets()
            loop = asyncio.get_event_loop()
            data = await asyncio.wait_for(
                loop.run_in_executor(None, _compute_sync, wallet, markets), timeout=_TIMEOUT)
        except Exception as e:
            logger.warning("active position summary failed for %s: %s", wallet[:10], e)
            data = _error_summary()
        else:
            await _enrich_opened_at(wallet, data.get("active_markets", []))
        # cache successes for _TTL, errors only briefly so they retry next load
        ttl = _ERR_TTL if data.get("active_positions_error") else _TTL
        _cache[wallet] = {"data": data, "ts": time.monotonic(), "ttl": ttl}
        return data


async def get_summaries(wallets: list[str]) -> dict[str, dict]:
    """Summaries for many wallets, AWAITED (computes misses). For small batches
    (e.g. watchlist) where the caller wants the data now."""
    uniq = list(dict.fromkeys((w or "").lower() for w in wallets if w))
    results = await asyncio.gather(*[get_summary(w) for w in uniq], return_exceptions=True)
    out: dict[str, dict] = {}
    for w, r in zip(uniq, results):
        out[w] = r if isinstance(r, dict) else _error_summary()
    return out


def get_cached(wallet: str) -> dict | None:
    """Return the cached summary if fresh, else None. NEVER blocks / computes."""
    wallet = (wallet or "").lower()
    c = _cache.get(wallet)
    if c and time.monotonic() - c["ts"] < c["ttl"]:
        return c["data"]
    return None


async def _fill(wallet: str) -> None:
    try:
        await get_summary(wallet)
    finally:
        _inflight.discard(wallet)


def prime(wallets: list[str]) -> None:
    """Fire-and-forget: schedule background computation for any uncached wallets
    (deduped via _inflight, bounded by the semaphore). Used by the discover list so
    the page never blocks — counts appear on a subsequent poll."""
    for w in wallets:
        w = (w or "").lower()
        if not w or get_cached(w) is not None or w in _inflight:
            continue
        _inflight.add(w)
        try:
            asyncio.get_event_loop().create_task(_fill(w))
        except Exception:
            _inflight.discard(w)
