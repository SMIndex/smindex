"""Wallet Explorer — HL side (WALLET_EXPLORER_REPORT Part 2).

Server-side aggregation for /wallet/:address. Every venue call goes through
the shared HL client at EXPLORER priority (lowest — shed first, never allowed
to crowd copy/live or the samplers). No venue call is ever made from the
frontend.

Tiers (the research doc's budget contract):
  * Tier 1 (public, cached 300s): resolution (userRole, 60 weight ONCE per
    address ever — persisted in explorer_address_meta) + clearinghouseState 2
    + frontendOpenOrders 20 (shared 300s profile cache) + portfolio 20
    (shared 600s cache) + spotClearinghouseState 2 + cohort context from our
    own tables (0). Cold cost ≤ ~46 weight (+60 first-ever resolution);
    warm cost 0.
  * Tier 2 (PUBLIC, lazy per tab): fills 600s cache, ledger/funding 600s,
    historical orders 1h, extras 1h. Paginated fetches carry a hard page cap
    and report truncation honestly. The connect-wallet gate was removed in
    the density pass — this data is public on the venues and every other
    protection (20 req/min/IP explorer bucket, cache headers, explorer-
    priority shedding in the shared HL client, lazy per-tab load) is
    unchanged, so the budget contract above still holds.

HIP-3 decision (build order allowed either): **canonical dex only** — we do
NOT enumerate builder-deployed dexes (perpDexs); every panel that could
differ is labeled "canonical dex only" via the `hip3` field. Cheaper and
honest; revisit when a real builder-dex wallet shows the need.

Honesty rules: absence is reported as absence ("No Hyperliquid account
found", empty tabs render empty states); every payload carries fetched_at +
cache provenance + the measured weight cost of the venue calls it actually
made this request.
"""
import re
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/explorer", tags=["explorer"])

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

TIER1_TTL = 300.0
TAB_TTL = 600.0
TAB_TTL_LONG = 3600.0
PAGE_CAP = 3                    # hard cap per paginated tab fetch
HIP3_LABEL = "canonical dex only"

_tier1_cache: dict[str, tuple[float, dict]] = {}
_spot_cache: dict[str, tuple[float, dict]] = {}
_vault_cache: dict[str, tuple[float, dict]] = {}
_tab_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _public_base() -> str:
    """Public site URL used in share-card metadata. Reads PERPL_TERMINAL_URL so
    it follows the env everywhere else uses."""
    import os
    return os.environ.get("PERPL_TERMINAL_URL", "https://smindex.xyz").rstrip("/")


def _norm(address: str) -> str:
    if not _ADDR_RE.match(address or ""):
        raise HTTPException(status_code=422, detail="Not a valid 0x address")
    return address.lower()


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class _Meter:
    """Measured weight of the venue calls THIS request actually made."""
    def __init__(self) -> None:
        self.weight = 0
        self.requests = 0

    def add(self, info_type: str, items: int = 0) -> None:
        self.requests += 1
        self.weight += hl_client.base_weight(info_type) + hl_client.extra_weight(info_type, items)


async def _post(meter: _Meter, payload: dict, timeout: float = 15.0):
    """Explorer-priority post with meter accounting. HLWeightShed → 503 with
    Retry-After (honest shed, never fabricated data)."""
    try:
        r = await hl_client.post_info(payload, priority=hl_client.EXPLORER,
                                      timeout=timeout)
    except hl_client.HLWeightShed as exc:
        logger.info("explorer shed: %s (%s)", payload.get("type"), exc)
        raise HTTPException(status_code=503, detail="Explorer budget exhausted — retry shortly",
                            headers={"Retry-After": "30"}) from exc
    if r.status_code == 429:
        raise HTTPException(status_code=503, detail="Venue rate limited — retry shortly",
                            headers={"Retry-After": r.headers.get("Retry-After") or "30"})
    r.raise_for_status()
    body = r.json()
    meter.add(str(payload.get("type") or ""), len(body) if isinstance(body, list) else 0)
    return body


# ---- resolution: userRole, cached PERMANENTLY (weight 60 once per address) --

async def _resolve(address: str, meter: _Meter) -> dict:
    sf = get_session_factory()
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT role, master_address, checked_at FROM explorer_address_meta "
            "WHERE address = :a"), {"a": address})).first()
    # 'unresolved' = a Perpl-side insert seeded the row before any userRole
    # fetch — NOT a cached resolution; fall through and resolve it now.
    if row and row[0] != "unresolved":
        return {"role": row[0], "master": row[1], "cached": True,
                "checked_at": row[2].isoformat() if row[2] else None}
    body = await _post(meter, {"type": "userRole", "user": address})
    role = str((body or {}).get("role") or "missing")[:16]
    data = (body or {}).get("data") or {}
    master = None
    for k in ("user", "master"):
        v = str(data.get(k) or "").lower()
        if _ADDR_RE.match(v):
            master = v
            break
    import json as _json
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO explorer_address_meta (address, role, master_address, raw, checked_at) "
            "VALUES (:a, :r, :m, :raw, UTC_TIMESTAMP()) "
            "ON DUPLICATE KEY UPDATE role=VALUES(role), master_address=VALUES(master_address), "
            "raw=VALUES(raw), checked_at=VALUES(checked_at)"),
            {"a": address, "r": role, "m": master, "raw": _json.dumps(body or {})})
        await s.commit()
    return {"role": role, "master": master, "cached": False,
            "checked_at": datetime.utcnow().isoformat()}


async def _vault_details(address: str, meter: _Meter) -> dict | None:
    now = time.monotonic()
    c = _vault_cache.get(address)
    if c and now - c[0] < TIER1_TTL:
        return c[1]
    try:
        body = await _post(meter, {"type": "vaultDetails", "vaultAddress": address})
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("vaultDetails failed for %s: %s", address[:10], exc)
        return None
    if not body:
        return None
    out = {
        "name": body.get("name"),
        "leader": str(body.get("leader") or "").lower() or None,
        "apr": _f(body.get("apr")),
        "follower_count": len(body.get("followers") or []) or None,
        "max_distributable": _f(body.get("maxDistributable")),
        "is_closed": body.get("isClosed"),
    }
    _vault_cache[address] = (now, out)
    return out


# ---- cohort context (0 venue requests — our own tables) ---------------------

async def _cohort_context(address: str) -> dict:
    sf = get_session_factory()
    out: dict = {"ranks": {}, "flags": {}, "fill_stats": None, "sweep": None,
                 "source": "our tables (leaderboard 1h ingest, 20-min sweep, hourly fill sampler)"}
    async with sf() as s:
        ranks = (await s.execute(text(
            "SELECT s.period, s.`rank`, s.pnl_total, s.roi, s.volume FROM leaderboard_snapshots s "
            "JOIN (SELECT period, MAX(timestamp) mt FROM leaderboard_snapshots "
            "      WHERE exchange='hl' GROUP BY period) lb "
            "  ON lb.period = s.period AND lb.mt = s.timestamp "
            "WHERE s.exchange='hl' AND s.wallet_address = :w"), {"w": address})).all()
        for period, rank, pnl, roi, vol in ranks:
            out["ranks"][period] = {"rank": rank, "pnl": _f(pnl), "roi": _f(roi),
                                    "volume": _f(vol)}
        prof = (await s.execute(text(
            "SELECT display_name FROM trader_profiles "
            "WHERE exchange='hl' AND wallet_address = :w"), {"w": address})).first()
        if prof:
            out["display_name"] = prof[0]
        fs = (await s.execute(text(
            "SELECT trades_7d, win_rate_7d, profit_factor, maker_ratio, "
            " realized_pnl_7d, max_drawdown_7d, sample_capped, computed_at "
            "FROM trader_fill_stats WHERE wallet_address = :w"), {"w": address})).first()
        if fs:
            out["fill_stats"] = {
                "trades_7d": fs[0], "win_rate_7d": _f(fs[1]),
                "profit_factor": _f(fs[2]), "maker_ratio": _f(fs[3]),
                "realized_pnl_7d": _f(fs[4]), "max_drawdown_7d": _f(fs[5]),
                "sample_capped": bool(fs[6]),
                "computed_at": fs[7].isoformat() if fs[7] else None,
            }
        sw = (await s.execute(text(
            "SELECT cycle_ts, account_value, gross_notional, n_assets "
            "FROM analytics_wallet_state WHERE wallet = :w "
            "ORDER BY cycle_ts DESC LIMIT 1"), {"w": address})).first()
        if sw:
            out["sweep"] = {"cycle_ts": sw[0].isoformat(), "account_value": _f(sw[1]),
                            "gross_notional": _f(sw[2]), "n_assets": sw[3]}
    # three-valued in-process verdicts (cohort module — unknown stays unknown)
    try:
        from app.services.analytics import cohort as an_cohort
        out["flags"]["mm"] = an_cohort.mm_flag(address)
        out["flags"]["hedger"] = an_cohort.hedger_flag(address)
        cw = {w.lower() for w in an_cohort.wallets()}
        # only assert membership when the cohort is actually loaded — an empty
        # list means "unknown", never "not in cohort"
        out["in_cohort"] = (address in cw) if cw else None
    except Exception:
        pass
    return out


# ---- Tier 1 ----------------------------------------------------------------

@router.get("/hl/{address}")
async def explorer_hl_tier1(address: str) -> dict:
    """Public headline aggregate. Cached 300s server-side; venue calls at
    EXPLORER priority through the shared weight budget."""
    address = _norm(address)
    now = time.monotonic()
    c = _tier1_cache.get(address)
    if c and now - c[0] < TIER1_TTL:
        return c[1]

    meter = _Meter()
    resolution = await _resolve(address, meter)
    out: dict = {
        "address": address,
        "resolution": resolution,
        "hip3": HIP3_LABEL,
        "provenance": {
            "state": "clearinghouseState + frontendOpenOrders (shared 300s cache)",
            "equity": "venue portfolio history (shared 600s cache) — includes unrealized · venue PnL",
            "spot": "spotClearinghouseState (300s cache)",
            "cohort": "our tables, zero venue calls",
        },
    }
    if resolution["role"] == "vault":
        out["vault"] = await _vault_details(address, meter)

    if resolution["role"] == "missing":
        # honest empty — still let the frontend check Perpl (Part 3)
        out.update({"hl_account": False, "fetched_at": time.time(),
                    "weight_cost": meter.weight, "requests": meter.requests})
        _tier1_cache[address] = (now, out)
        return out

    from app.services.hyperliquid import profile as hl_profile
    state_cached = address in hl_profile._cache and \
        now - hl_profile._cache[address][0] < hl_profile._CACHE_TTL
    try:
        prof = await hl_profile.get_profile_state(address, deep=False,
                                                  priority=hl_client.EXPLORER)
    except hl_client.HLWeightShed:
        raise HTTPException(status_code=503, detail="Explorer budget exhausted — retry shortly",
                            headers={"Retry-After": "30"})
    if not state_cached:
        meter.add("clearinghouseState")
        meter.add("frontendOpenOrders")

    # spot split (2 weight, 300s cache). Balances are reported RAW (coin,
    # total, entryNtl); only USDC is a USD value by identity — we never price
    # spot coins with perp mids here (unit mismatch risk, hedger.py doc).
    spot = None
    sc = _spot_cache.get(address)
    if sc and now - sc[0] < TIER1_TTL:
        spot = sc[1]
    else:
        try:
            body = await _post(meter, {"type": "spotClearinghouseState", "user": address})
            balances = [
                {"coin": b.get("coin"), "total": _f(b.get("total")),
                 "hold": _f(b.get("hold")), "entry_ntl": _f(b.get("entryNtl"))}
                for b in (body or {}).get("balances", [])
                if _f(b.get("total"), 0) and _f(b.get("total"), 0) > 0
            ]
            usdc = next((b["total"] for b in balances if b["coin"] == "USDC"), 0.0)
            spot = {"balances": balances, "usdc": usdc,
                    "note": "spot balances raw; only USDC shown as USD"}
            _spot_cache[address] = (now, spot)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("spot state failed for %s: %s", address[:10], exc)
            spot = None   # honest: unavailable, not zero

    # equity curve: every period incl. perp-only variants
    equity = None
    pf_cached = address in hl_profile._portfolio_cache and \
        now - hl_profile._portfolio_cache[address][0] < hl_profile._PORTFOLIO_TTL
    try:
        equity = await hl_profile.get_portfolio_full(address, priority=hl_client.EXPLORER)
        if not pf_cached:
            meter.add("portfolio")
    except hl_client.HLWeightShed:
        equity = None
    except Exception as exc:
        logger.warning("portfolio failed for %s: %s", address[:10], exc)

    cohort = await _cohort_context(address)

    out.update({
        "hl_account": True,
        "account_value": prof.get("account_value"),
        "withdrawable": prof.get("withdrawable"),
        "total_margin_used": prof.get("total_margin_used"),
        "total_ntl_pos": prof.get("total_ntl_pos"),
        "maintenance_margin": prof.get("maintenance_margin"),
        "positions": prof.get("positions", []),
        "adds": prof.get("adds", []),
        "pending_entries": prof.get("pending_entries", []),
        "copyable_pct": prof.get("copyable_pct"),
        "spot": spot,
        "equity": equity,
        "equity_label": "includes unrealized · venue PnL",
        "cohort": cohort,
        "state_fetched_at": prof.get("fetched_at"),
        "fetched_at": time.time(),
        "weight_cost": meter.weight,
        "requests": meter.requests,
    })
    _tier1_cache[address] = (now, out)
    return out


# ---- Tier 2 (public, lazy per tab) -----------------------------------------------

async def _tab(address: str, kind: str, ttl: float, fetch) -> dict:
    key = (kind, address)
    now = time.monotonic()
    c = _tab_cache.get(key)
    if c and now - c[0] < ttl:
        return c[1]
    data = await fetch()
    _tab_cache[key] = (now, data)
    return data


async def _paged(meter: _Meter, info_type: str, address: str,
                 start_ms: int, end_ms: int, extra: dict | None = None) -> tuple[list, bool]:
    """Forward-paginate a time-range info type (venue caps 500/2000 per
    response). PAGE_CAP hard cap; returns (items, truncated)."""
    items: list = []
    cursor = start_ms
    truncated = False
    for page in range(PAGE_CAP):
        body = await _post(meter, {
            "type": info_type, "user": address,
            "startTime": int(cursor), "endTime": int(end_ms), **(extra or {})},
            timeout=30.0)
        batch = body or []
        items.extend(batch)
        if len(batch) < 500:
            break
        times = [int(e.get("time") or 0) for e in batch if e.get("time")]
        if not times:
            break
        cursor = max(times) + 1
        if page == PAGE_CAP - 1:
            truncated = True
    return items, truncated


@router.get("/hl/{address}/fills")
async def explorer_hl_fills(address: str, days: int = Query(7, ge=1, le=90)) -> dict:
    address = _norm(address)

    async def fetch() -> dict:
        meter = _Meter()
        now_ms = int(time.time() * 1000)
        # userFillsByTime pages at 2000/response
        items: list = []
        cursor = now_ms - days * 86400_000
        truncated = False
        for page in range(PAGE_CAP):
            body = await _post(meter, {
                "type": "userFillsByTime", "user": address,
                "startTime": int(cursor), "endTime": now_ms,
                "aggregateByTime": True}, timeout=30.0)
            batch = body or []
            items.extend(batch)
            if len(batch) < 2000:
                break
            cursor = max(int(e.get("time") or 0) for e in batch) + 1
            if page == PAGE_CAP - 1:
                truncated = True
        fills = [{
            "time": f.get("time"), "coin": f.get("coin"), "dir": f.get("dir"),
            "px": _f(f.get("px")), "sz": _f(f.get("sz")),
            "closed_pnl": _f(f.get("closedPnl")), "crossed": f.get("crossed"),
            "fee": _f(f.get("fee")), "fee_token": f.get("feeToken"),
        } for f in items]
        return {"fills": fills, "count": len(fills), "truncated": truncated,
                "window_days": days, "aggregated_by_time": True,
                "retention_note": f"last {len(fills)} fills · venue retains 10k",
                "fetched_at": time.time(), "weight_cost": meter.weight}

    return await _tab(address, f"fills{days}", TAB_TTL, fetch)


@router.get("/hl/{address}/ledger")
async def explorer_hl_ledger(address: str, days: int = Query(90, ge=1, le=365)) -> dict:
    address = _norm(address)

    async def fetch() -> dict:
        meter = _Meter()
        now_ms = int(time.time() * 1000)
        items, truncated = await _paged(meter, "userNonFundingLedgerUpdates",
                                        address, now_ms - days * 86400_000, now_ms)
        deposits = withdrawals = 0.0
        rows = []
        for e in items:
            d = e.get("delta") or {}
            t = str(d.get("type") or "")
            usdc = _f(d.get("usdc"), 0.0) or 0.0
            if t == "deposit":
                deposits += usdc
            elif t == "withdraw":
                withdrawals += abs(usdc)
            rows.append({"time": e.get("time"), "type": t, "usdc": usdc,
                         "raw": {k: v for k, v in d.items() if k not in ("type", "usdc")}})
        return {"entries": rows, "count": len(rows), "truncated": truncated,
                "net_deposits": round(deposits - withdrawals, 2),
                "deposits": round(deposits, 2), "withdrawals": round(withdrawals, 2),
                "window_days": days,
                "note": "PnL vs net capital in — net deposits over the window, not lifetime unless the window covers it",
                "fetched_at": time.time(), "weight_cost": meter.weight}

    return await _tab(address, f"ledger{days}", TAB_TTL, fetch)


@router.get("/hl/{address}/funding")
async def explorer_hl_funding(address: str, days: int = Query(7, ge=1, le=90)) -> dict:
    address = _norm(address)

    async def fetch() -> dict:
        meter = _Meter()
        now_ms = int(time.time() * 1000)
        items, truncated = await _paged(meter, "userFunding", address,
                                        now_ms - days * 86400_000, now_ms)
        by_coin: dict[str, float] = {}
        rows = []
        for e in items:
            d = e.get("delta") or {}
            coin = str(d.get("coin") or "").upper()
            usdc = _f(d.get("usdc"), 0.0) or 0.0
            by_coin[coin] = by_coin.get(coin, 0.0) + usdc
            rows.append({"time": e.get("time"), "coin": coin, "usdc": usdc,
                         "szi": _f(d.get("szi")), "rate": _f(d.get("fundingRate"))})
        return {"entries": rows[-500:], "count": len(rows), "truncated": truncated,
                "total_by_coin": {c: round(v, 4) for c, v in by_coin.items()},
                "total": round(sum(by_coin.values()), 4), "window_days": days,
                "fetched_at": time.time(), "weight_cost": meter.weight}

    return await _tab(address, f"funding{days}", TAB_TTL, fetch)


@router.get("/hl/{address}/orders")
async def explorer_hl_orders(address: str) -> dict:
    address = _norm(address)

    async def fetch() -> dict:
        meter = _Meter()
        body = await _post(meter, {"type": "historicalOrders", "user": address},
                           timeout=30.0)
        rows = []
        for o in (body or []):
            inner = o.get("order") or {}
            rows.append({
                "coin": inner.get("coin"), "side": inner.get("side"),
                "limit_px": _f(inner.get("limitPx")), "sz": _f(inner.get("sz")),
                "orig_sz": _f(inner.get("origSz")),
                "order_type": inner.get("orderType"),
                "reduce_only": inner.get("reduceOnly"),
                "is_trigger": inner.get("isTrigger"),
                "trigger_px": _f(inner.get("triggerPx")),
                "timestamp": inner.get("timestamp"),
                "status": o.get("status"),
                "status_ts": o.get("statusTimestamp"),
            })
        return {"orders": rows, "count": len(rows),
                "note": "venue returns most recent orders with status reasons",
                "fetched_at": time.time(), "weight_cost": meter.weight}

    return await _tab(address, "orders", TAB_TTL_LONG, fetch)


@router.get("/hl/{address}/extras")
async def explorer_hl_extras(address: str) -> dict:
    address = _norm(address)

    async def fetch() -> dict:
        meter = _Meter()
        out: dict = {"fetched_at": time.time()}
        try:
            fees = await _post(meter, {"type": "userFees", "user": address})
            out["fees"] = {
                "cross_rate": _f((fees or {}).get("userCrossRate")),
                "add_rate": _f((fees or {}).get("userAddRate")),
                "daily_volume_14d": [
                    {"date": d.get("date"), "exchange": _f(d.get("exchange")),
                     "user_cross": _f(d.get("userCross")), "user_add": _f(d.get("userAdd"))}
                    for d in ((fees or {}).get("dailyUserVlm") or [])[-14:]],
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("userFees failed for %s: %s", address[:10], exc)
            out["fees"] = None
        try:
            ve = await _post(meter, {"type": "userVaultEquities", "user": address})
            out["vault_equities"] = [
                {"vault": v.get("vaultAddress"), "equity": _f(v.get("equity"))}
                for v in (ve or [])]
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("userVaultEquities failed for %s: %s", address[:10], exc)
            out["vault_equities"] = None
        try:
            dg = await _post(meter, {"type": "delegations", "user": address})
            out["staking"] = [
                {"validator": d.get("validator"), "amount": _f(d.get("amount")),
                 "locked_until": d.get("lockedUntilTimestamp")}
                for d in (dg or [])]
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("delegations failed for %s: %s", address[:10], exc)
            out["staking"] = None
        out["weight_cost"] = meter.weight
        return out

    return await _tab(address, "extras", TAB_TTL_LONG, fetch)


# ============================================================================
# Perpl side (Part 3) — on-chain live state through the EXISTING reader.
#
# Chain-read discipline: explorer reads run through _chain_call — one at a
# time (semaphore) so profile/tracker chain reads win the RPC budget, with
# retry-with-jitter when the public Monad RPC rate-limits (429). The account
# id (getAccountByAddr) is persisted FOREVER once non-zero (an account id
# never changes); a zero id is cached in-process for an hour (the wallet may
# create an account later — never persisted as a permanent "no").
#
# Liquidation price: getLiquidationInfo(perpId) turned out to be a MARKET
# parameter view (fee splits / BTL threshold — now exposed under liq_params),
# NOT a per-position price. The per-position liq price uses the documented
# formula (chain_reader.liq_price_for) with maintenanceMarginHdths from the
# live market configs. Documented deviation from the build order's wording.
# ============================================================================
import asyncio as _asyncio
import random as _random

PERPL_TTL = 300.0
_PERPL_NOACCT_TTL = 3600.0
_perpl_cache: dict[str, tuple[float, dict]] = {}
_perpl_noacct: dict[str, float] = {}
_chain_gate = _asyncio.Semaphore(1)
_CHAIN_RETRIES = 2

HISTORY_NOTE = ("Trade & transfer history requires the on-chain indexer — "
                "indexing begins Part 4")


async def _chain_call(fn, *args):
    """Executor + explorer gate + retry-with-jitter on RPC 429s."""
    loop = _asyncio.get_event_loop()
    async with _chain_gate:
        for attempt in range(_CHAIN_RETRIES + 1):
            try:
                return await loop.run_in_executor(None, fn, *args)
            except Exception as exc:
                if "429" in str(exc) and attempt < _CHAIN_RETRIES:
                    await _asyncio.sleep(1.5 * (attempt + 1) + _random.uniform(0, 1.0))
                    continue
                raise


async def _perpl_account_id(address: str) -> int:
    """Account id, persisted forever once known non-zero (migration v19)."""
    sf = get_session_factory()
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT perpl_account_id FROM explorer_address_meta WHERE address = :a"),
            {"a": address})).first()
    if row and row[0]:
        return int(row[0])
    now = time.monotonic()
    if address in _perpl_noacct and now - _perpl_noacct[address] < _PERPL_NOACCT_TTL:
        return 0
    from app.services import chain_reader
    aid = await _chain_call(chain_reader.get_perpl_account_id, address)
    if aid:
        async with sf() as s:
            # never clobbers the HL role fields on an existing row
            await s.execute(text(
                "INSERT INTO explorer_address_meta (address, role, checked_at, perpl_account_id) "
                "VALUES (:a, 'unresolved', UTC_TIMESTAMP(), :id) "
                "ON DUPLICATE KEY UPDATE perpl_account_id = VALUES(perpl_account_id)"),
                {"a": address, "id": aid})
            await s.commit()
    else:
        _perpl_noacct[address] = now
    return int(aid)


async def _perpl_leaderboard(address: str) -> dict:
    """Live ranks (all/day x pnl/vol via the router's 30s cache) + top-20
    snapshot history for this wallet (30-min snapshots). Zero chain calls."""
    from app.routers.leaders import _get_live_leaderboard
    ranks: dict = {}
    for period in ("all", "day"):
        for sorting in ("pnl", "vol"):
            try:
                rows = await _get_live_leaderboard(period, sorting)
            except Exception:
                rows = []
            hit = next((t for t in rows
                        if str(t.get("wallet_address", "")).lower() == address), None)
            if hit:
                ranks[f"{period}_{sorting}"] = {
                    "rank": hit.get("rank"), "pnl": _f(hit.get("pnl_total")),
                    "roi": _f(hit.get("roi")), "volume": _f(hit.get("volume"))}
    sf = get_session_factory()
    async with sf() as s:
        hist = (await s.execute(text(
            "SELECT timestamp, `rank`, pnl_total FROM leaderboard_snapshots "
            "WHERE exchange = 'perpl' AND wallet_address = :w AND period = 'all' "
            "ORDER BY timestamp DESC LIMIT 96"), {"w": address})).all()
        n_app = (await s.execute(text(
            "SELECT COUNT(*), MIN(timestamp) FROM leaderboard_snapshots "
            "WHERE exchange = 'perpl' AND wallet_address = :w"), {"w": address})).first()
    return {
        "ranks": ranks,
        "on_leaderboard": bool(ranks),
        "snapshot_history": [
            {"t": ts.isoformat(), "rank": rk, "pnl": _f(pnl)}
            for ts, rk, pnl in reversed(hist)],
        "snapshot_appearances": int(n_app[0]) if n_app else 0,
        "snapshot_since": n_app[1].isoformat() if n_app and n_app[1] else None,
        "snapshot_note": "top-20 only · 30-min snapshots",
    }


@router.get("/perpl/{address}")
async def explorer_perpl(address: str) -> dict:
    """Public Perpl live state: account, positions (with formula-derived liq
    price), resting orders incl. TP/SL triggers (getPerpOrderLocks path via
    the EXISTING get_trader_detail — not duplicated), leaderboard windows +
    snapshot history. History pre-indexer: honest note, never dashes."""
    address = _norm(address)
    now = time.monotonic()
    c = _perpl_cache.get(address)
    if c and now - c[0] < PERPL_TTL:
        return c[1]

    from app.services import chain_reader
    try:
        account_id = await _perpl_account_id(address)
    except Exception as exc:
        logger.warning("perpl account lookup failed for %s: %s", address[:10], exc)
        raise HTTPException(status_code=503, detail="Monad RPC unavailable — retry shortly",
                            headers={"Retry-After": "30"})

    out: dict = {
        "address": address,
        "history_note": HISTORY_NOTE,
        "provenance": {
            "state": "on-chain (getAccountByAddr / getPosition / getPerpOrderLocks via the shared reader)",
            "liq_price": "formula: entry ± (deposit − MMR)/size, MMR = notional × 100/maintenanceMarginHdths (live market config)",
            "leaderboard": "Perpl leaderboard API (30s cache) + our 30-min top-20 snapshots",
        },
    }
    if account_id == 0:
        out.update({"perpl_account": False, "account_id": 0,
                    "leaderboard": await _perpl_leaderboard(address),
                    "fetched_at": time.time()})
        _perpl_cache[address] = (now, out)
        return out

    detail = await _chain_call(chain_reader.get_trader_detail, address)
    if detail is None:
        # account exists (id != 0) — a None here is a chain-read failure,
        # never "flat": report unavailable instead of inventing emptiness
        raise HTTPException(status_code=503, detail="Chain read failed — retry shortly",
                            headers={"Retry-After": "30"})

    # liq price per position (formula) + market liq parameters (the view)
    from app.services.ws_manager import ws_manager as _wm
    liq_params: dict[int, dict | None] = {}
    for p in detail.get("positions", []):
        mm_hdths = 2000
        mm_src = "default"
        if _wm and p["market_id"] in getattr(_wm, "_market_configs", {}):
            mm_hdths = _wm._market_configs[p["market_id"]].get("maintenance_margin", 2000)
            mm_src = "market config"
        p["liq_price"] = chain_reader.liq_price_for(p, mm_hdths)
        p["liq_source"] = f"formula ({mm_src} mm={mm_hdths})"
        p["collateral"] = p.get("deposit")
        if p["market_id"] not in liq_params:
            try:
                liq_params[p["market_id"]] = await _chain_call(
                    chain_reader.get_liquidation_info, p["market_id"])
            except Exception:
                liq_params[p["market_id"]] = None

    orders = detail.get("orders", [])
    for o in orders:
        o["kind"] = ("stop_loss" if o.get("order_type") == "stop_loss"
                     else "take_profit" if o.get("order_type") == "take_profit"
                     else "limit")

    out.update({
        "perpl_account": True,
        "account_id": account_id,
        "balance": detail.get("balance"),
        "margin_used": detail.get("margin_used"),
        "positions": detail.get("positions", []),
        "orders": orders,
        "liq_params": {str(k): v for k, v in liq_params.items()},
        "leaderboard": await _perpl_leaderboard(address),
        "fetched_at": time.time(),
    })
    _perpl_cache[address] = (now, out)
    return out


# ============================================================================
# Perpl indexed history + cross-venue activity (Part 4). Both read OUR MySQL
# sink (perpl_events, filled by indexer/perpl_indexer.py) — zero venue calls
# beyond the HL ledger reuse in /activity. Auth-gated like every heavy tab.
# ============================================================================

async def _indexer_state() -> dict | None:
    sf = get_session_factory()
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT last_block, head_block, first_event_block, note, updated_at "
            "FROM perpl_indexer_state WHERE id = 1"))).first()
    if not row:
        return None
    pct = None
    if row[0] and row[1] and row[2] and int(row[1]) > int(row[2]):
        pct = round(100.0 * (int(row[0]) - int(row[2])) / (int(row[1]) - int(row[2])), 2)
    return {"last_block": row[0], "head_block": row[1],
            "first_event_block": row[2], "backfill_pct": pct, "note": row[3],
            "updated_at": row[4].isoformat() if row[4] else None}


async def _perpl_events_for(address: str, limit: int = 300) -> tuple[list[dict], str | None]:
    """Events for an address: rows attributed to its account(s) plus rows this
    address SENT (tx_from) that carry no account attribution. Returns
    (events, indexed_from_iso)."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT at, block, tx, event_name, event_type, market_id, amount, "
            " balance_after, fee, bfa, attributed, (address IS NULL) via_tx_from "
            "FROM perpl_events "
            "WHERE address = :a OR (address IS NULL AND tx_from = :a) "
            "ORDER BY at DESC, log_index DESC LIMIT :n"),
            {"a": address, "n": limit})).all()
        first = (await s.execute(text(
            "SELECT MIN(at) FROM perpl_events "
            "WHERE address = :a OR (address IS NULL AND tx_from = :a)"),
            {"a": address})).scalar()
    def _fl(v):
        return float(v) if v is not None else None
    # via_tx_from rows were merely SENT by this address (no account
    # attribution) — an operator/batch sender may carry other accounts'
    # events, so they are flagged and their balance_after is suppressed
    # (it belongs to whichever account the event settled, not the sender).
    events = [{
        "at": r[0].isoformat(), "block": r[1], "tx": r[2],
        "event_name": r[3], "event_type": r[4], "market_id": r[5],
        "amount": _fl(r[6]), "balance_after": None if r[11] else _fl(r[7]),
        "fee": _fl(r[8]), "bfa": _fl(r[9]), "attributed": bool(r[10]),
        "via_tx_from": bool(r[11]),
    } for r in rows]
    return events, (first.isoformat() if first else None)


@router.get("/perpl/{address}/history")
async def explorer_perpl_history(address: str, limit: int = Query(300, ge=1, le=1000)) -> dict:
    """Indexed Perpl history from perpl_events + honest indexer status. The
    equity series is the on-chain ACCOUNT BALANCE after balance-affecting
    events (balance_after) — labelled as such: it excludes margin locked in
    positions and unrealized PnL."""
    address = _norm(address)
    state = await _indexer_state()
    events, indexed_from = await _perpl_events_for(address, limit)
    equity = [{"t": e["at"], "v": e["balance_after"]}
              for e in reversed(events) if e["balance_after"] is not None]
    status_line = None
    if state is None or (state.get("note") or "").startswith("awaiting"):
        status_line = ("Indexer awaiting its Envio API token (owner PENDING) — "
                       "history fills in once indexing starts")
    elif state.get("backfill_pct") is not None and state["backfill_pct"] < 100:
        status_line = (f"indexed from {indexed_from or 'genesis'} · backfill "
                       f"{state['backfill_pct']}% complete")
    elif indexed_from:
        status_line = f"indexed from {indexed_from} · backfill complete"
    return {
        "address": address, "events": events, "count": len(events),
        "indexed_from": indexed_from, "indexer": state,
        "status_line": status_line,
        "equity": equity,
        "equity_label": "on-chain account balance after each event — excludes "
                        "margin locked in positions and unrealized PnL",
        "provenance": "perpl_events (Envio HyperSync indexer, indexer/perpl_indexer.py)",
        "fetched_at": time.time(),
    }


@router.get("/activity/{address}")
async def explorer_activity(address: str, days: int = Query(90, ge=1, le=365)) -> dict:
    """Cross-venue activity: HL non-funding ledger (existing cached tab fetch)
    + Perpl indexed events, merged into ONE venue-tagged event model, newest
    first. Each side that is unavailable says so — never silently empty."""
    address = _norm(address)
    merged: list[dict] = []
    hl_note = None
    try:
        hl = await explorer_hl_ledger(address, days=days)
        for e in hl.get("entries", []):
            merged.append({
                "venue": "hl",
                "at": datetime.utcfromtimestamp((e.get("time") or 0) / 1000).isoformat(),
                "type": e.get("type"), "amount": e.get("usdc"),
            })
    except HTTPException as exc:
        hl_note = f"HL ledger unavailable ({exc.status_code})"
    except Exception:
        hl_note = "HL ledger unavailable"
    perpl_events, indexed_from = await _perpl_events_for(address, 500)
    cutoff = datetime.utcnow().timestamp() - days * 86400
    perpl_note = None
    state = await _indexer_state()
    if state is None or (state.get("note") or "").startswith("awaiting"):
        perpl_note = "Perpl side pending — indexer awaiting its Envio API token"
    for e in perpl_events:
        try:
            ts = datetime.fromisoformat(e["at"]).timestamp()
        except ValueError:
            continue
        if ts < cutoff:
            continue
        merged.append({"venue": "perpl", "at": e["at"], "type": e["event_type"],
                       "amount": e["bfa"] if e["bfa"] is not None else e["amount"],
                       "market_id": e["market_id"], "fee": e["fee"]})
    merged.sort(key=lambda x: x["at"], reverse=True)
    return {
        "address": address, "events": merged[:600], "count": len(merged),
        "window_days": days,
        "hl_note": hl_note, "perpl_note": perpl_note,
        "perpl_indexed_from": indexed_from,
        "fetched_at": time.time(),
    }


# ---- OG share cards (Part 5): nginx routes /wallet/0x… here for ALL agents —
# the SPA shell is returned with per-wallet OG/Twitter meta injected, so link
# unfurlers (which run no JS) render a card while browsers boot the app
# normally. Zero venue calls — enrichment is DB-only (display name, HL rank).
from pathlib import Path as _Path

from fastapi.responses import HTMLResponse

_DIST_INDEX = _Path(__file__).resolve().parents[3] / "frontend" / "dist" / "index.html"
_og_cache: dict[str, tuple[float, str]] = {}
_OG_TTL = 600.0


@router.get("/page/{address}", response_class=HTMLResponse)
async def explorer_og_page(address: str) -> HTMLResponse:
    address = _norm(address)
    now = time.monotonic()
    c = _og_cache.get(address)
    if c and now - c[0] < _OG_TTL:
        return HTMLResponse(c[1])
    try:
        html = _DIST_INDEX.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=404, detail="SPA shell not found")
    short = f"{address[:6]}…{address[-4:]}"
    name = short
    extra = ""
    try:
        sf = get_session_factory()
        async with sf() as s:
            dn = (await s.execute(text(
                "SELECT display_name FROM trader_profiles "
                "WHERE exchange='hl' AND wallet_address = :w AND display_name IS NOT NULL"),
                {"w": address})).scalar()
            if dn:
                name = f"{dn} ({short})"
            rk = (await s.execute(text(
                "SELECT s.period, s.`rank` FROM leaderboard_snapshots s "
                "JOIN (SELECT period, MAX(timestamp) mt FROM leaderboard_snapshots "
                "      WHERE exchange='hl' GROUP BY period) lb "
                "  ON lb.period = s.period AND lb.mt = s.timestamp "
                "WHERE s.exchange='hl' AND s.wallet_address = :w "
                "ORDER BY s.`rank` ASC LIMIT 1"), {"w": address})).first()
            if rk:
                extra = f" · HL leaderboard {rk[0]} #{rk[1]}"
    except Exception:
        pass
    title = f"{name} on SMINDEX"
    desc = (f"Live Hyperliquid + Perpl state, equity and on-chain history for "
            f"{short}{extra}. Freshness and provenance on every panel.")
    url = f"{_public_base()}/wallet/{address}"
    tags = (
        f'<meta property="og:title" content="{title}"/>'
        f'<meta property="og:description" content="{desc}"/>'
        f'<meta property="og:url" content="{url}"/>'
        '<meta property="og:type" content="website"/>'
        '<meta property="og:site_name" content="SMINDEX"/>'
        f'<meta property="og:image" content="{_public_base()}/brand/og-card.png"/>'
        '<meta property="og:image:width" content="1200"/>'
        '<meta property="og:image:height" content="630"/>'
        '<meta name="twitter:card" content="summary_large_image"/>'
        f'<meta name="twitter:image" content="{_public_base()}/brand/og-card.png"/>'
        f'<meta name="twitter:title" content="{title}"/>'
        f'<meta name="twitter:description" content="{desc}"/>'
    )
    html = html.replace("</head>", tags + "</head>", 1)
    _og_cache[address] = (now, html)
    return HTMLResponse(html)
