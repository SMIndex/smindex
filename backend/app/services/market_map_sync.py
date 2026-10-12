"""Keep market_map (HL coin -> Perpl market id) in step with the live Perpl
registry, so a new Perpl listing becomes copyable without a code change.

Why this exists: the map was seeded by hand in migration v2 with the five
markets Perpl had in August 2026. Perpl grew to eleven; the map did not. The
auto-copy market gate reads this table, so every leader entry in MON, LIT, VVV,
PUMP, NEAR and UNI was rejected as "not listed on Perpl" - 16 entries between
2026-09-29 and 2026-10-09, none of them a risk decision. The cross-venue basis
gate reads the same table and would have failed the same entries for the same
reason.

A row is written only when BOTH venues really have the symbol: the Perpl
registry says the market is active, and Hyperliquid's allMids stream is
actually quoting that coin name. Nothing is guessed, and a cold HL stream makes
this a no-op rather than a wrong answer. When a symbol's Perpl market goes away
its perpl_market_id is set to NULL - both readers treat NULL as "not copyable"
- so a delisting can never leave us pointing at a dead market id.
"""
import asyncio

from sqlalchemy import text

from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

# allMids arrives as one full-universe snapshot (230+ coins), so a cache
# smaller than this means the stream has not primed yet.
MIN_HL_COINS = 20


async def sync(markets: list[dict]) -> dict:
    """Reconcile market_map against the normalized registry list.
    Returns what changed; writes nothing when nothing changed."""
    out: dict = {"added": [], "repointed": [], "unmapped": [], "skipped": None}
    if not markets:
        out["skipped"] = "registry empty"
        return out

    from app.services.hyperliquid.prices import hl_price_cache
    hl = {c.upper() for c in hl_price_cache}
    if len(hl) < MIN_HL_COINS:
        out["skipped"] = f"HL allMids not primed ({len(hl)} coins)"
        return out

    active = {str(m["symbol"]).upper(): int(m["market_id"]) for m in markets
              if m.get("is_active") and m.get("market_id") is not None and m.get("symbol")}
    want = {sym: mid for sym, mid in active.items() if sym in hl}

    sf = get_session_factory()
    async with sf() as s:
        have = {str(r[0]).upper(): r[1] for r in (await s.execute(text(
            "SELECT native_symbol, perpl_market_id FROM market_map WHERE exchange = 'hl'"
        ))).all()}
        for sym, mid in want.items():
            if have.get(sym) == mid:
                continue
            if sym in have:
                await s.execute(text(
                    "UPDATE market_map SET perpl_market_id = :m "
                    "WHERE exchange = 'hl' AND native_symbol = :n"), {"m": mid, "n": sym})
                out["repointed"].append(f"{sym} {have[sym]}->{mid}")
            else:
                await s.execute(text(
                    "INSERT INTO market_map (exchange, native_symbol, perpl_market_id, canonical_symbol) "
                    "VALUES ('hl', :n, :m, :n)"), {"n": sym, "m": mid})
                out["added"].append(f"{sym}:{mid}")
        for sym, mid in have.items():
            if mid is not None and sym not in active:
                await s.execute(text(
                    "UPDATE market_map SET perpl_market_id = NULL "
                    "WHERE exchange = 'hl' AND native_symbol = :n"), {"n": sym})
                out["unmapped"].append(f"{sym} was {mid}")
        if out["added"] or out["repointed"] or out["unmapped"]:
            await s.commit()
            logger.info("market_map sync: %s", {k: v for k, v in out.items() if v})
    return out


# --- heartbeat ---------------------------------------------------------------
# refresh_markets() runs once at boot and then only when a request happens to
# need the registry, and the boot run lands while the HL allMids stream is
# still connecting - so the hook alone would leave the map to chance. This
# loop guarantees the reconciliation runs, starting once allMids has had time
# to prime.

INTERVAL_SEC = 600
FIRST_RUN_DELAY_SEC = 90

_task = None


async def _loop(stop) -> None:
    await asyncio.sleep(FIRST_RUN_DELAY_SEC)
    while not stop.is_set():
        try:
            # refresh_markets() calls sync() through its own hook, so one call
            # keeps the registry, the chain reader and the map in step.
            from app.services import market_registry
            await market_registry.refresh_markets(force=True)
        except Exception:
            logger.exception("market_map heartbeat failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass


def start(stop) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop(stop))
        logger.info("market_map sync started (every %ss, first run in %ss)",
                    INTERVAL_SEC, FIRST_RUN_DELAY_SEC)
