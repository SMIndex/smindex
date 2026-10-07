"""Smart-money cohort definition (ANALYTICS_BUILD_REPORT Phase 1.1).

Base cohort = distinct wallets present in the LATEST HL leaderboard ingest
batch of each of the four windows (day/week/month/all), capped at TOP_N by
all-time PnL rank. Wallets absent from the 'all' window rank AFTER every
'all'-ranked wallet, ordered by their best rank in any other window (stable,
documented — the cap has to cut somewhere and lifetime PnL is the cohort's
organizing principle).

Per-wallet MM flag (three-valued, unknown != false):
  is_likely_mm = TRUE  when (maker_ratio >= MM_MAKER_RATIO_MIN AND
                             trades_7d   >= MM_TRADES_7D_MIN)      [fill stats]
                    OR (gross position notional >= MM_NOTIONAL_X * account
                        value across >= MM_MIN_ASSETS assets)      [sweep state]
  is_likely_mm = FALSE when fill stats exist and clause A fails and the last
                 sweep state exists and clause B fails
  is_likely_mm = NULL  when neither source can prove or disprove (no fill
                 stats and no sweep state yet). The core cohort EXCLUDES only
                 TRUE; the API reports how many NULL-unknowns are included.

Clause A is computed here from trader_fill_stats (Tier-2 sampler covers the
top-50 union, so deep-cohort wallets are usually NULL there). Clause B is
computed by the position sweep from the clearinghouseState it already fetched
(account value + per-asset notionals) and pushed back via note_sweep_state().

Refresh runs after each hourly HL leaderboard ingest and lazily on first use
after boot. Pure DB reads — zero venue requests.
"""
import asyncio
import time

from sqlalchemy import text

from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

TOP_N = 400
MM_MAKER_RATIO_MIN = 0.6
MM_TRADES_7D_MIN = 500
MM_NOTIONAL_X = 25.0     # gross notional >= 25x account value ...
MM_MIN_ASSETS = 8        # ... spread across >= 8 assets

# wallet -> {"rank": int, "mm_fillstats": bool|None}
_cohort: dict[str, dict] = {}
_order: list[str] = []                   # ranked wallet order (rank 1 first)
# Clause-B evidence from the sweep: wallet -> (is_mm_by_notional, checked_at)
_sweep_mm: dict[str, tuple[bool, float]] = {}
# Tier-2 C1: hedger verdicts hydrated from analytics_wallet_flags at refresh
# (three-valued: absent = unknown; the spot sampler writes the table)
_hedger: dict[str, bool] = {}
_refreshed_at: float | None = None
_refresh_lock = asyncio.Lock()


async def refresh() -> dict:
    """Recompute the cohort from the latest ingest batches. Logged per spec:
    cohort size, mm-flagged count, unknown count."""
    global _cohort, _order, _refreshed_at
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT s.wallet_address w, s.period p, s.`rank` r "
            "FROM leaderboard_snapshots s JOIN ("
            "  SELECT period, MAX(timestamp) mt FROM leaderboard_snapshots "
            "  WHERE exchange = 'hl' GROUP BY period"
            ") lb ON lb.period = s.period AND lb.mt = s.timestamp "
            "WHERE s.exchange = 'hl'"
        ))).all()

    all_rank: dict[str, int] = {}
    best_other: dict[str, int] = {}
    for w, p, r in rows:
        w = w.lower()
        if p == "all":
            all_rank[w] = min(all_rank.get(w, 10 ** 9), int(r))
        else:
            best_other[w] = min(best_other.get(w, 10 ** 9), int(r))

    ranked = sorted(all_rank, key=lambda w: all_rank[w])
    extras = sorted((w for w in best_other if w not in all_rank),
                    key=lambda w: best_other[w])
    order = (ranked + extras)[:TOP_N]

    # Clause A from fill stats (one bulk read; wallets absent there stay None)
    fillmap: dict[str, dict] = {}
    if order:
        placeholders = ",".join(f":w{i}" for i in range(len(order)))
        params = {f"w{i}": w for i, w in enumerate(order)}
        sf = get_session_factory()
        async with sf() as s:
            frows = (await s.execute(text(
                "SELECT wallet_address, maker_ratio, trades_7d "
                f"FROM trader_fill_stats WHERE wallet_address IN ({placeholders})"
            ), params)).all()
        for w, mr, t7 in frows:
            fillmap[w.lower()] = {"maker_ratio": mr, "trades_7d": t7}

    cohort: dict[str, dict] = {}
    for i, w in enumerate(order):
        fs = fillmap.get(w)
        mm_a: bool | None = None
        if fs is not None and fs["maker_ratio"] is not None:
            mm_a = (float(fs["maker_ratio"]) >= MM_MAKER_RATIO_MIN
                    and int(fs["trades_7d"] or 0) >= MM_TRADES_7D_MIN)
        cohort[w] = {"rank": i + 1, "mm_fillstats": mm_a}

    _cohort = cohort
    _order = order
    _refreshed_at = time.time()

    # Clause-B restart persistence (owner-approved 2026-08-26): the sweep's
    # per-cycle analytics_wallet_state rows carry exactly the clause-B inputs
    # (account_value, gross_notional, n_assets), so hydrate _sweep_mm from
    # each wallet's LATEST row instead of losing coverage on every reboot.
    # In-memory evidence from the current process stays authoritative (it is
    # never older than the stored rows). Wallets whose account value was <= 0
    # (not persisted) simply re-evaluate on the next sweep cycle — rare,
    # documented.
    if order:
        try:
            placeholders = ",".join(f":w{i}" for i in range(len(order)))
            params = {f"w{i}": w for i, w in enumerate(order)}
            sf = get_session_factory()
            async with sf() as s:
                wrows = (await s.execute(text(
                    "SELECT s.wallet, s.account_value, s.gross_notional, s.n_assets, "
                    " UNIX_TIMESTAMP(s.cycle_ts) ct "
                    "FROM analytics_wallet_state s JOIN ("
                    "  SELECT wallet, MAX(cycle_ts) mt FROM analytics_wallet_state "
                    f"  WHERE wallet IN ({placeholders}) GROUP BY wallet"
                    ") l ON l.wallet = s.wallet AND l.mt = s.cycle_ts"), params)).all()
            hydrated = 0
            for w, av, gross, n_assets, ct in wrows:
                wl = w.lower()
                if wl in _sweep_mm:
                    continue   # live evidence from this process wins
                av = float(av or 0)
                is_mm = (av > 0 and float(gross) >= MM_NOTIONAL_X * av
                         and int(n_assets) >= MM_MIN_ASSETS)
                _sweep_mm[wl] = (is_mm, float(ct))
                hydrated += 1
            if hydrated:
                logger.info("cohort clause-B hydrated from wallet_state: %d wallets", hydrated)
        except Exception:
            logger.exception("clause-B hydration failed (non-fatal — flags rebuild per cycle)")

    # Tier-2 C1: hydrate hedger verdicts (bulk read; absent rows = unknown)
    global _hedger
    try:
        from app.services.analytics import hedger as hedger_mod
        _hedger = await hedger_mod.flags_bulk(order)
    except Exception:
        logger.exception("hedger flag hydration failed (non-fatal — flags stay unknown)")

    flags = {w: mm_flag(w) for w in order}
    hflags = {w: hedger_flag(w) for w in order}
    stats = {
        "size": len(order),
        "mm_flagged": sum(1 for v in flags.values() if v is True),
        "mm_unknown": sum(1 for v in flags.values() if v is None),
        "hedger_flagged": sum(1 for v in hflags.values() if v is True),
        "hedger_unknown": sum(1 for v in hflags.values() if v is None),
    }
    logger.info("analytics cohort refresh: %s", stats)
    return stats


async def ensure() -> None:
    """Lazy boot path: refresh once if never refreshed this process."""
    if _refreshed_at is None:
        async with _refresh_lock:
            if _refreshed_at is None:
                await refresh()


def note_sweep_state(wallet: str, account_value: float,
                     asset_notionals: dict[str, float]) -> None:
    """Clause-B evidence pushed by the position sweep from state it already
    fetched (no extra venue calls). Gross notional across all assets."""
    gross = sum(abs(n) for n in asset_notionals.values())
    is_mm = (account_value > 0
             and gross >= MM_NOTIONAL_X * account_value
             and len([n for n in asset_notionals.values() if abs(n) > 0]) >= MM_MIN_ASSETS)
    _sweep_mm[wallet.lower()] = (is_mm, time.time())


def mm_flag(wallet: str) -> bool | None:
    """Three-valued MM verdict combining both clauses (module doc)."""
    w = wallet.lower()
    entry = _cohort.get(w)
    a = entry["mm_fillstats"] if entry else None
    b_pair = _sweep_mm.get(w)
    b = b_pair[0] if b_pair else None
    if a is True or b is True:
        return True
    if a is False and b is False:
        return False
    if a is False and b is None:
        return None   # clause B never evaluated — cannot rule MM out fully
    if a is None and b is False:
        return None   # no fill stats — cannot rule out clause A
    return None


def hedger_flag(wallet: str) -> bool | None:
    """Tier-2 C1 three-valued hedger verdict: True/False from the spot
    sampler's stored classification; None = never checked (unknown)."""
    return _hedger.get(wallet.lower())


def wallets() -> list[str]:
    """Ranked cohort wallets (rank 1 first)."""
    return list(_order)


def rank_of(wallet: str) -> int | None:
    e = _cohort.get(wallet.lower())
    return e["rank"] if e else None


def summary() -> dict:
    flags = {w: mm_flag(w) for w in _order}
    hflags = {w: hedger_flag(w) for w in _order}
    return {
        "size": len(_order),
        "mm_flagged": sum(1 for v in flags.values() if v is True),
        "mm_unknown": sum(1 for v in flags.values() if v is None),
        "hedger_flagged": sum(1 for v in hflags.values() if v is True),
        "hedger_unknown": sum(1 for v in hflags.values() if v is None),
        "refreshed_at": _refreshed_at,
    }
