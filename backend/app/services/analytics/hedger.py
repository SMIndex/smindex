"""Hedger classification (ANALYTICS_TIER2_REPORT Part C1).

A cohort wallet is a LIKELY HEDGER when its spot book offsets its perp book:
spot long ~ perp short (within HEDGE_TOL of each other's notional) on >= 1
asset with >= HEDGE_MIN_NOTIONAL matched. Such wallets' perp positions are
inventory hedges, not directional bets — the CORE cohort excludes them like
MMs (three-valued: unknown stays unknown, never guessed).

Budget discipline: this is a NEW budgeted consumer of `spotClearinghouseState`
— hard cap SPOT_BUDGET (60) requests per hourly run, sequential, paced,
logged. It is FOLDED INTO the existing fill_stats rotation scheduler (no new
task): fill_stats.run_once() calls run_pass() with its own separate budget
after the fills work. Wallet selection: never-checked cohort wallets by rank
first, then oldest checked_at (staleness refresh, RECHECK_DAYS).

Classification inputs (zero EXTRA venue calls beyond the one spot fetch):
  * spot balances from the fetched spotClearinghouseState (coin units).
  * spot->perp coin mapping is EXPLICIT and majors-only (SPOT_TO_PERP below):
    HL spot tickers (UBTC, UETH, ...) differ from perp tickers, and
    k-prefixed perps (kBONK = 1000x) have a unit mismatch we refuse to
    guess at. Unmapped spot coins are ignored — a hedge we cannot price is
    not evidence either way (documented limitation).
  * spot notional = units x the local ws-fed perp mid (zero requests);
    no mid -> coin skipped.
  * perp side/notional per asset from the wallet's LATEST analytics_positions
    cycle rows (DB).

Verdict per wallet (stored in analytics_wallet_flags, migration v12):
  True    >= 1 asset where min(spot_long_ntl, perp_short_ntl) >=
          HEDGE_MIN_NOTIONAL and the two notionals are within HEDGE_TOL
          (|spot - perp| / max(spot, perp) <= 0.25).
  False   spot state fetched fine and no asset matches (incl. flat-perp or
          empty-spot wallets).
  NULL    never checked, or the fetch failed — unknown stays unknown.
Evidence JSON stores every matched asset with both notionals.
"""
import asyncio
import json
import time
from datetime import datetime

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

SPOT_BUDGET = 60                 # hard cap per hourly fill_stats run
PAUSE_SEC = 1.2                  # same pacing as every other sampler
RATE_LIMIT_BACKOFF_SEC = 20      # one backoff; a second 429 ends the pass
HEDGE_TOL = 0.25                 # |spot - perp| / max <= 25%
HEDGE_MIN_NOTIONAL = 100_000.0   # matched leg must be >= $100k
RECHECK_DAYS = 7                 # stale verdicts get revisited

# Majors-only spot->perp mapping (unit-safe; k-prefixed perps deliberately
# absent — kBONK is 1000x BONK and UBONK units would misprice 1000x).
SPOT_TO_PERP = {
    "UBTC": "BTC", "UETH": "ETH", "USOL": "SOL", "UPUMP": "PUMP",
    "UFART": "FARTCOIN", "HYPE": "HYPE", "PURR": "PURR",
}


def _mid(coin: str) -> float | None:
    from app.services.hyperliquid import prices as hl_prices
    return hl_prices.get_mid(coin)


async def _perp_book(wallet: str) -> dict[str, dict] | None:
    """{asset: {side, notional}} from the wallet's latest sweep cycle rows.
    None when the wallet has no rows at all (never swept -> unknown)."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT p.asset, p.side, p.notional FROM analytics_positions p "
            "JOIN (SELECT MAX(cycle_ts) mt FROM analytics_positions "
            "      WHERE wallet = :w) l ON l.mt = p.cycle_ts "
            "WHERE p.wallet = :w"), {"w": wallet})).all()
    if not rows:
        return None
    return {a: {"side": sd, "notional": float(n)}
            for a, sd, n in rows if sd != "flat"}


def classify(spot_balances: list[dict], perp: dict[str, dict]) -> tuple[bool, list[dict]]:
    """(is_hedger, evidence). Pure — unit-testable against real payloads."""
    matches: list[dict] = []
    for b in spot_balances:
        perp_coin = SPOT_TO_PERP.get(str(b.get("coin", "")))
        if not perp_coin:
            continue
        try:
            units = float(b.get("total") or 0)
        except (TypeError, ValueError):
            continue
        if units <= 0:
            continue
        p = perp.get(perp_coin)
        if not p or p["side"] != "short":
            continue
        mid = _mid(perp_coin)
        if not mid:
            continue          # cannot price honestly -> not evidence
        spot_ntl = units * mid
        perp_ntl = p["notional"]
        if min(spot_ntl, perp_ntl) < HEDGE_MIN_NOTIONAL:
            continue
        if abs(spot_ntl - perp_ntl) / max(spot_ntl, perp_ntl) <= HEDGE_TOL:
            matches.append({"asset": perp_coin,
                            "spot_notional": round(spot_ntl, 2),
                            "perp_short_notional": round(perp_ntl, 2)})
    return (len(matches) > 0, matches)


async def _targets(cohort_wallets: list[str], limit: int) -> list[str]:
    """Never-checked first (cohort rank order), then oldest checked_at."""
    if not cohort_wallets:
        return []
    placeholders = ",".join(f":w{i}" for i in range(len(cohort_wallets)))
    params = {f"w{i}": w for i, w in enumerate(cohort_wallets)}
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet, hedger_checked_at FROM analytics_wallet_flags "
            f"WHERE wallet IN ({placeholders})"), params)).all()
    checked = {w.lower(): ts for w, ts in rows}
    never = [w for w in cohort_wallets if w.lower() not in checked]
    stale = sorted((w for w in cohort_wallets if w.lower() in checked),
                   key=lambda w: checked[w.lower()] or datetime.min)
    # stale wallets only re-checked past RECHECK_DAYS
    stale = [w for w in stale
             if checked[w.lower()] is None
             or (datetime.utcnow() - checked[w.lower()]).days >= RECHECK_DAYS]
    return (never + stale)[:limit]


async def run_pass() -> dict:
    """One budgeted pass, called from fill_stats.run_once() (no new task).
    Returns the stats dict fill_stats merges into its run log."""
    from app.services.analytics import cohort
    t0 = time.monotonic()
    await cohort.ensure()
    targets = await _targets(cohort.wallets(), SPOT_BUDGET)
    requests = 0
    checked = 0
    flagged = 0
    failed = 0
    rate_limited_once = False
    sf = get_session_factory()
    for w in targets:
        if requests >= SPOT_BUDGET:
            break
        requests += 1     # reserved BEFORE the attempt — the cap is hard
        try:
            resp = await hl_client.post_info({
                "type": "spotClearinghouseState", "user": w},
                priority=hl_client.BACKGROUND, timeout=30.0)
            if resp.status_code == 429:
                if rate_limited_once:
                    logger.warning("hedger pass: second 429 — ending pass")
                    break
                rate_limited_once = True
                logger.warning("hedger pass: 429 — backing off %ss", RATE_LIMIT_BACKOFF_SEC)
                await asyncio.sleep(RATE_LIMIT_BACKOFF_SEC)
                continue     # this wallet waits for a later pass
            resp.raise_for_status()
            balances = (resp.json() or {}).get("balances", [])
        except Exception as exc:
            failed += 1
            logger.debug("hedger fetch failed for %s: %s (verdict stays unknown)",
                         w[:10], exc)
            continue
        perp = await _perp_book(w)
        if perp is None:
            # never swept: we cannot compare books — unknown, but record the
            # check time so the sampler doesn't spin on it every hour
            perp = {}
        is_hedger, evidence = classify(balances, perp)
        async with sf() as s:
            await s.execute(text(
                "INSERT INTO analytics_wallet_flags "
                "(wallet, is_likely_hedger, hedger_evidence, hedger_checked_at) "
                "VALUES (:w, :h, :e, :t) "
                "ON DUPLICATE KEY UPDATE is_likely_hedger=VALUES(is_likely_hedger), "
                " hedger_evidence=VALUES(hedger_evidence), "
                " hedger_checked_at=VALUES(hedger_checked_at)"),
                {"w": w.lower(), "h": 1 if is_hedger else 0,
                 "e": json.dumps(evidence) if evidence else None,
                 "t": datetime.utcnow()})
            await s.commit()
        checked += 1
        if is_hedger:
            flagged += 1
            logger.info("hedger flagged: %s — %s", w[:10], evidence)
        await asyncio.sleep(PAUSE_SEC)
    stats = {"spot_requests": requests, "spot_checked": checked,
             "spot_hedgers_flagged": flagged, "spot_failed": failed,
             "spot_budget": SPOT_BUDGET,
             "spot_duration_sec": round(time.monotonic() - t0, 1)}
    return stats


async def flags_bulk(wallets: list[str]) -> dict[str, bool]:
    """{wallet: is_likely_hedger} for wallets that HAVE a verdict (absent =
    unknown — three-valued by omission)."""
    wl = [w.lower() for w in wallets]
    if not wl:
        return {}
    placeholders = ",".join(f":w{i}" for i in range(len(wl)))
    params = {f"w{i}": w for i, w in enumerate(wl)}
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet, is_likely_hedger FROM analytics_wallet_flags "
            f"WHERE wallet IN ({placeholders}) AND is_likely_hedger IS NOT NULL"),
            params)).all()
    return {w.lower(): bool(h) for w, h in rows}
