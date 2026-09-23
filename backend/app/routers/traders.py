"""Trader discovery + profile read paths (copy v1).

Reads the live Perpl leaderboard (via the existing leaders helper) and persists
trader_profiles / trader_stats_daily. Read-only: no orders, no chain writes.
On-chain positions are read-only via chain_reader.
"""
import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.services.copy import trader_profiles
from app.services import active_positions
from app.services import equity as equity_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/traders", tags=["traders-v1"])


async def _optional_user(authorization: str | None = Header(default=None)):
    from app.routers.auth import get_optional_user
    try:
        return await get_optional_user(authorization)
    except Exception:
        return None


async def _caller_is_admin(user) -> bool:
    from app.config import settings
    return bool(user) and user.wallet_address.lower() in settings.admin_address_set


def _sorting(sort: str) -> str:
    # analytics sorts reorder the pnl-ranked board (base data unchanged)
    if sort in ("pnl", "pnl_total", "pnl_7d", "pnl_30d",
                "recent_activity", "active", "consistent", "win_rate"):
        return "pnl"
    return "vol"


VALID_EXCHANGES = {"perpl", "hl"}


def _exchange(exchange: str) -> str:
    e = (exchange or "perpl").lower()
    if e not in VALID_EXCHANGES:
        raise HTTPException(status_code=400, detail=f"Unknown exchange: {exchange}")
    return e


@router.get("")
async def list_traders(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort: str = Query("pnl"),
    period: str = Query("all"),
    exchange: str = Query("perpl", description="perpl | hl"),
    with_positions: bool = Query(True, description="include live active-position summary"),
    wallets: str | None = Query(None, description=(
        "Comma-separated addresses (cap 50): return EXACTLY these wallets in the "
        "given order, ignoring the board's rank limit. Wallets absent from this "
        "window's board are looked up in the other HL windows' stored batches "
        "(DB/cache only). Rank badge = this window's board position, else the "
        "analytics cohort rank. Serves the analytics 'Copyable on Perpl' deep "
        "link — a linked wallet is never silently dropped.")),
) -> list[dict]:
    """Discovery list. perpl = live Perpl leaderboard passthrough (unchanged);
    hl = latest hourly DB ingest of the Hyperliquid leaderboard. Upserts
    profiles (exchange-scoped; never resurrects hidden ones) and, for perpl,
    attaches each trader's live active-position summary."""
    from app.routers.leaders import _get_live_leaderboard

    exch = _exchange(exchange)
    traders = await _get_live_leaderboard(period, _sorting(sort), exchange=exch)
    if exch == "perpl":
        # hidden-profile exclusion (hl rows are pre-filtered in the loader)
        hidden = await trader_profiles.hidden_wallets("perpl")
        if hidden:
            traders = [t for t in traders if t["wallet_address"].lower() not in hidden]

    # Analytics sorts: reorder the whole board via ONE SQL over its wallets
    # BEFORE pagination (no N+1). Wallets missing metrics keep board order
    # after the ranked ones (sorted() is stable).
    board_wallets = [t["wallet_address"] for t in traders]
    ordered: list[str] | None = None
    if sort == "recent_activity":
        ordered = await trader_profiles.recent_activity_order(board_wallets, exchange=exch)
    elif sort in ("active", "consistent"):
        from app.services import activity_metrics
        ordered = await activity_metrics.activity_order(board_wallets, exch, sort)
    elif sort == "win_rate" and exch == "hl":
        # eligibility: win_rate non-NULL AND trades_7d >= 20; ineligible rows
        # stay below in board order (frontend shows why on hover)
        from app.services.hyperliquid import fill_stats as hl_fill_stats
        ordered = await hl_fill_stats.win_rate_order(board_wallets)
    if ordered is not None:
        pos = {w: i for i, w in enumerate(ordered)}
        traders = sorted(traders, key=lambda t: pos.get(t["wallet_address"].lower(), len(pos)))

    # ---- address-list mode (analytics deep link) ---------------------------
    # Every requested wallet renders or is honestly absent from ALL stored
    # batches — the board's rank limit never drops one. No pagination here:
    # the request IS the page (capped at 50 addresses).
    explicit_ranks: dict[str, int] | None = None
    if wallets:
        import re as _re
        req = [w.strip().lower() for w in wallets.split(",")
               if _re.fullmatch(r"0x[0-9a-fA-F]{40}", w.strip())][:50]
        board_pos = {t["wallet_address"].lower(): i + 1
                     for i, t in enumerate(traders)}
        by_wallet = {t["wallet_address"].lower(): t for t in traders}
        fallback_rank: dict[str, int] = {}
        missing = [w for w in req if w not in by_wallet]
        if exch == "hl" and missing:
            from app.routers.leaders import _get_live_leaderboard as _lb
            for p in ("all", "month", "week", "day"):
                if not missing or p == period:
                    continue
                try:
                    rows_p = await _lb(p, "pnl", exchange="hl")
                except Exception:
                    continue
                bp = {t["wallet_address"].lower(): (t, i + 1)
                      for i, t in enumerate(rows_p)}
                for w in list(missing):
                    if w in bp:
                        by_wallet[w] = dict(bp[w][0])
                        fallback_rank[w] = bp[w][1]   # rank in that window
                        missing.remove(w)
        cohort_rank = {}
        try:
            from app.services.analytics import cohort as an_cohort
            await an_cohort.ensure()
            cohort_rank = {w: an_cohort.rank_of(w) for w in req}
        except Exception:
            pass
        selected: list[dict] = []
        explicit_ranks = {}
        for w in req:
            t = by_wallet.get(w)
            if t is None:
                continue   # nowhere in stored batches — honestly absent
            r = board_pos.get(w) or cohort_rank.get(w) or fallback_rank.get(w)
            if r is None:
                continue   # no honest rank to show — skip rather than invent
            explicit_ranks[w] = int(r)
            selected.append(t)
        traders = selected
        page = traders                      # requested order, no pagination
    else:
        page = traders[skip: skip + limit]

    # ONE bulk upsert for the whole page — the old per-row loop was 50
    # sequential sessions/commits and dominated list latency (~40s observed).
    try:
        profs = await trader_profiles.upsert_profiles_bulk(
            [t["wallet_address"] for t in page], exchange=exch)
    except Exception:
        logger.exception("bulk profile upsert failed")
        profs = {}
    # analytics attach — two bulk reads for the page, keyed by wallet
    from app.services import activity_metrics
    try:
        act = await activity_metrics.get_metrics_bulk(
            [t["wallet_address"] for t in page], exch)
    except Exception:
        logger.exception("activity metrics attach failed")
        act = {}
    fstats: dict = {}
    if exch == "hl":
        try:
            from app.services.hyperliquid import fill_stats as hl_fill_stats
            fstats = await hl_fill_stats.get_stats_bulk(
                [t["wallet_address"] for t in page])
        except Exception:
            logger.exception("fill stats attach failed")

    out: list[dict] = []
    for i, t in enumerate(page):
        wl = t["wallet_address"].lower()
        prof = profs.get(wl) or {}
        out.append({
            **t,
            "exchange": exch,
            "rank": (explicit_ranks.get(wl) if explicit_ranks is not None
                     else skip + i + 1),
            "display_name": prof.get("display_name"),
            "is_verified": prof.get("is_verified", False),
            "source": prof.get("source", "leaderboard"),
            "last_fill_at": prof.get("last_fill_at"),
            "activity": act.get(wl),
            "fill_stats": fstats.get(wl),
        })

    # Active-position summaries are Perpl on-chain reads — HL rows carry no
    # fake position data (the UI renders the column only when present).
    if exch != "perpl":
        with_positions = False

    # Attach live active-position summary — CACHE-ONLY + background prime so the
    # discover page never blocks on on-chain reads. Uncached wallets come back
    # 'pending' and fill in on a subsequent poll (see frontend).
    if with_positions and out:
        wallets = [t["wallet_address"] for t in out]
        active_positions.prime(wallets)   # fire-and-forget compute for misses
        for t in out:
            s = active_positions.get_cached(t["wallet_address"])
            if s is None:
                t["active_positions_count"] = None
                t["active_markets"] = []
                t["has_active_positions"] = False
                t["active_positions_pending"] = True
            else:
                t["active_positions_count"] = s.get("active_positions_count")
                t["active_markets"] = s.get("active_markets", [])
                t["has_active_positions"] = s.get("has_active_positions", False)
                if s.get("active_positions_error"):
                    t["active_positions_error"] = True
    return out


@router.get("/active-summary")
async def active_summary(
    wallets: str = Query(..., description="comma-separated wallet addresses (max 50)"),
) -> dict:
    """Batch live active-position summaries keyed by wallet (for watchlist/lists)."""
    wlist = [w.strip() for w in wallets.split(",") if w.strip()][:50]
    return await active_positions.get_summaries(wlist)


@router.get("/equity")
async def equity_batch(
    wallets: str = Query(..., description="comma-separated wallets (max 50)"),
    timeframe: str = Query("7d", description="24h|7d|30d|all"),
    points: int = Query(60, ge=8, le=400),
    days: int | None = Query(None, ge=1, le=365, description="legacy override; prefer timeframe"),
    period: str = Query("all"),
    exchange: str = Query("perpl"),
) -> dict:
    """Batch REAL cumulative-PnL series (from leaderboard_snapshots) keyed by wallet,
    covering the selected timeframe (downsampled to `points`, first+last preserved)."""
    wlist = [w.strip() for w in wallets.split(",") if w.strip()][:50]
    return await equity_service.get_series_batch(wlist, timeframe=timeframe, points=points, period=period, days=days, exchange=_exchange(exchange))


@router.get("/hl-active")
async def hl_active_batch(
    wallets: str = Query(..., description="comma-separated wallet addresses (max 25)"),
) -> dict:
    """Batch open-position summaries for HL list rows: {wallet: {count, markets,
    pending}}. Cache-only (shares the 300s hl profile cache) + background prime,
    same progressive contract as the Perpl active-position columns."""
    from app.services.hyperliquid import profile as hl_profile
    wlist = [w.strip() for w in wallets.split(",") if w.strip()][:25]
    return hl_profile.get_active_batch(wlist)


async def _leaderboard_entry(wallet: str, exchange: str = "perpl") -> dict | None:
    from app.routers.leaders import _get_live_leaderboard
    addr = wallet.lower()
    periods = ("all", "day") if exchange == "perpl" else ("all", "day", "week", "month")
    for period in periods:
        traders = await _get_live_leaderboard(period, "pnl", exchange=exchange)
        for idx, t in enumerate(traders):
            if t["wallet_address"].lower() == addr:
                return {**t, "rank": idx + 1}
    return None


@router.get("/{wallet}")
async def get_trader(wallet: str, exchange: str = Query("perpl"),
                     request_user=Depends(_optional_user)) -> dict:
    """Trader profile + current headline stats. Upserts profile + (perpl only)
    today's stat row. Hidden profiles 404 for the public."""
    exch = _exchange(exchange)
    entry = await _leaderboard_entry(wallet, exchange=exch)
    # Always ensure a profile row exists (even off-leaderboard traders).
    profile = await trader_profiles.upsert_profile(wallet, exchange=exch)
    if profile.get("is_hidden") and not await _caller_is_admin(request_user):
        raise HTTPException(status_code=404, detail="Trader not found")

    # Activity analytics for the profile modal (both exchanges; NULLs stay NULL)
    from app.services import activity_metrics
    try:
        activity = (await activity_metrics.get_metrics_bulk([wallet], exch)).get(wallet.lower())
    except Exception:
        activity = None
    fill_stats = None
    if exch == "hl":
        try:
            from app.services.hyperliquid import fill_stats as hl_fill_stats
            fill_stats = (await hl_fill_stats.get_stats_bulk([wallet])).get(wallet.lower())
        except Exception:
            fill_stats = None

    if exch != "perpl":
        # trader_stats_daily has no exchange column (out of migration-v2 scope)
        # so daily stat rows stay Perpl-only.
        return {
            "profile": profile,
            "stats": {
                "pnl_total": entry.get("pnl_total") if entry else None,
                "roi": entry.get("roi") if entry else None,
                "volume": entry.get("volume") if entry else None,
                "rank": entry.get("rank") if entry else None,
                "on_leaderboard": entry is not None,
            },
            "activity": activity,
            "fill_stats": fill_stats,
        }

    if entry:
        try:
            await trader_profiles.upsert_daily_stat(
                wallet,
                pnl=entry.get("pnl_total"),
                roi=entry.get("roi"),
                volume=entry.get("volume"),
                rank=entry.get("rank"),
            )
        except Exception:
            logger.exception("upsert_daily_stat failed for %s", wallet[:10])

    return {
        "profile": profile,
        "stats": {
            "pnl_total": entry.get("pnl_total") if entry else None,
            "roi": entry.get("roi") if entry else None,
            "volume": entry.get("volume") if entry else None,
            "rank": entry.get("rank") if entry else None,
            "on_leaderboard": entry is not None,
        },
        "activity": activity,
        "fill_stats": fill_stats,
    }


@router.get("/{wallet}/stats")
async def get_trader_stats(wallet: str, days: int = Query(30, ge=1, le=365)) -> list[dict]:
    """Daily stats series from trader_stats_daily. Empty list until populated."""
    return await trader_profiles.get_stats_series(wallet, days)


@router.get("/{wallet}/equity")
async def get_trader_equity(
    wallet: str,
    timeframe: str = Query("7d", description="24h|7d|30d|all"),
    points: int = Query(160, ge=8, le=400),
    days: int | None = Query(None, ge=1, le=365, description="legacy override; prefer timeframe"),
    period: str = Query("all"),
    exchange: str = Query("perpl"),
) -> dict:
    """REAL cumulative-PnL series for one trader, for the profile equity curve.
    Perpl: our leaderboard_snapshots history (unchanged). HL: the venue's own
    `portfolio` pnlHistory — full history, timestamped points [{t, v}]."""
    exch = _exchange(exchange)
    if exch == "hl":
        from app.services.hyperliquid import profile as hl_profile
        try:
            pts = await hl_profile.get_portfolio_series(wallet, timeframe)
        except Exception as exc:
            logger.warning("hl portfolio series failed for %s: %s", wallet[:10], exc)
            pts = []
        return {"wallet": wallet.lower(), "timeframe": timeframe, "period": period,
                "exchange": exch, "points": pts, "timed": True}
    series = await equity_service.get_series(wallet, timeframe=timeframe, points=points, period=period, days=days, exchange=exch)
    return {"wallet": wallet.lower(), "timeframe": timeframe, "period": period, "exchange": exch, "points": series}


@router.get("/{wallet}/hl-state")
async def get_trader_hl_state(wallet: str) -> dict:
    """Hyperliquid profile depth: live positions (incl. liquidation price),
    grouped TP/SL, resting adds, pending trigger entries, copyable-on-Perpl %.
    300s in-process cache per wallet. Read-only public HL info API."""
    from app.services.hyperliquid import profile as hl_profile
    prof = await trader_profiles.get_profile(wallet, exchange="hl")
    if prof and prof.get("is_hidden"):
        raise HTTPException(status_code=404, detail="Trader not found")
    try:
        return await hl_profile.get_profile_state(wallet)
    except Exception as exc:
        logger.warning("hl-state failed for %s: %s", wallet[:10], exc)
        raise HTTPException(status_code=502, detail="Hyperliquid state unavailable")


@router.get("/{wallet}/hl-history")
async def get_trader_hl_history(wallet: str) -> dict:
    """Per-position build histories (build_history module) — served from the
    in-process cache the dating worker fills from its single userFills fetch;
    this endpoint itself makes ZERO venue calls. pending=True while the
    worker is still deriving (the drawer retries briefly)."""
    from app.services.hyperliquid import profile as hl_profile
    from app.services.hyperliquid import build_history
    prof = await trader_profiles.get_profile(wallet, exchange="hl")
    if prof and prof.get("is_hidden"):
        raise HTTPException(status_code=404, detail="Trader not found")
    w = (wallet or "").lower()
    histories = build_history.get(w)
    pending = histories is None and w in hl_profile._dating_inflight
    age = build_history.age_sec(w)
    return {
        "wallet": w,
        "pending": pending,
        "worker_inflight": w in hl_profile._dating_inflight,
        "fills_age_sec": round(age, 1) if age is not None else None,
        "histories": [h for h in (histories or {}).values()],
    }


@router.get("/{wallet}/positions")
async def get_trader_positions(wallet: str) -> dict:
    """Live on-chain open positions (read-only). Never places orders.

    Uses the SAME source as the Discover/Watchlist cards (active_positions, which
    reads via the dynamic market registry) so the profile and the cards always agree
    (e.g. HYPE shows, delisted SOL is never probed)."""
    summary = await active_positions.get_summary(wallet)
    return {
        "wallet": wallet.lower(),
        "positions": summary.get("active_markets", []),
        "active_positions_count": summary.get("active_positions_count"),
        "has_active_positions": summary.get("has_active_positions", False),
        "active_positions_error": bool(summary.get("active_positions_error")),
    }
