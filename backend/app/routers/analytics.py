"""Smart-money analytics API (ANALYTICS_BUILD_REPORT Phase 1.5).

Namespace /api/analytics/*. HARD RULE (spec): no endpoint may trigger venue
calls on the request path — every response is served from the DB written by
the 20-minute sweep, the in-process cycle cache, or the local HL mid cache
(fed by the existing allMids ws stream; reading it costs zero requests).

Every response carries computed_at (the cycle each number came from) and
resolution. In-process response cache is keyed by (endpoint, params,
latest_cycle_ts) — a new cycle invalidates everything by construction.

Registration is gated by settings.ANALYTICS_ENABLED (default true).
"""
import json
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.analytics import cohort
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])

LOW_SAMPLE_MIN = 5          # spec 1.6: < 5 cohort positions => "(low sample)"
COHORT_TOPS = {10, 50, 100, 400}
LEV_BUCKETS = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 20), (20, 1000)]
ENTRY_BUCKET_COUNT = 12
LIQ_BUCKETS_PER_SIDE = 3    # Design Guide B3.1: 3 buckets per side max
LIQ_NEAR_PCT = 0.40         # owner review: near band = mark ±40%; beyond -> one far row
STANCE_DEAD_ZONE = 0.10     # tile stance: |notional skew| <= 10% = balanced

_resp_cache: dict[tuple, tuple[float, dict]] = {}
_RESP_CACHE_MAX = 256


async def _latest_cycle() -> datetime | None:
    sf = get_session_factory()
    async with sf() as s:
        return (await s.execute(text(
            "SELECT MAX(cycle_ts) FROM analytics_asset_rollups"))).scalar()


def _cache_get(key: tuple):
    hit = _resp_cache.get(key)
    return hit[1] if hit else None


def _cache_put(key: tuple, value: dict) -> None:
    if len(_resp_cache) > _RESP_CACHE_MAX:
        oldest = sorted(_resp_cache.items(), key=lambda kv: kv[1][0])[:_RESP_CACHE_MAX // 2]
        for k, _ in oldest:
            _resp_cache.pop(k, None)
    _resp_cache[key] = (time.time(), value)


def _mid_local(asset: str) -> float | None:
    """Local ws-fed mid cache — zero venue requests."""
    from app.services.hyperliquid import prices as hl_prices
    return hl_prices.get_mid(asset)


@router.get("/status")
async def status() -> dict:
    """Cohort + sweep observability (freshness labels feed from here)."""
    from app.services.analytics import position_sweep, smi_study
    from app.services.analytics import depth as depth_mod
    await cohort.ensure()
    cy = await _latest_cycle()
    return {
        "cohort": cohort.summary(),
        "sweep": position_sweep.get_last_run(),
        "smi_study": smi_study.get_last_run(),
        "latest_cycle": cy.isoformat() if cy else None,
        "endpoint_latency": depth_mod.latency_stats(),
    }


@router.get("/assets")
async def list_assets() -> dict:
    """Latest rollup per asset, both cohort variants, venue context attached."""
    await cohort.ensure()
    cy = await _latest_cycle()
    if cy is None:
        return {"assets": [], "computed_at": None, "resolution": "20m",
                "cohort": cohort.summary()}
    key = ("assets", cy.isoformat())
    cached = _cache_get(key)
    if cached is not None:
        return cached
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT asset, cohort_variant, wallets_long, wallets_short, "
            " notional_long, notional_short, avg_lev_long, avg_lev_short, "
            " upnl_long, upnl_short, cohort_oi, venue_oi, venue_funding, "
            " fresh_pct_24h, flags "
            "FROM analytics_asset_rollups WHERE cycle_ts = :cy"), {"cy": cy}
        )).mappings().all()
    by_asset: dict[str, dict] = {}
    for r in rows:
        a = by_asset.setdefault(r["asset"], {"asset": r["asset"]})
        flags = json.loads(r["flags"]) if r["flags"] else {}
        a[r["cohort_variant"]] = {
            "wallets_long": r["wallets_long"], "wallets_short": r["wallets_short"],
            "notional_long": float(r["notional_long"]),
            "notional_short": float(r["notional_short"]),
            "avg_lev_long": float(r["avg_lev_long"]) if r["avg_lev_long"] is not None else None,
            "avg_lev_short": float(r["avg_lev_short"]) if r["avg_lev_short"] is not None else None,
            "upnl_long": float(r["upnl_long"]), "upnl_short": float(r["upnl_short"]),
            "cohort_oi": float(r["cohort_oi"]),
            "low_sample": (r["wallets_long"] + r["wallets_short"]) < LOW_SAMPLE_MIN,
            "age": {"dated": flags.get("dated"), "undated": flags.get("undated"),
                    "fresh_pct_24h": float(r["fresh_pct_24h"]) if r["fresh_pct_24h"] is not None else None},
        }
        a["venue"] = {"oi_usd": float(r["venue_oi"]) if r["venue_oi"] is not None else None,
                      "funding_hourly": float(r["venue_funding"]) if r["venue_funding"] is not None else None,
                      "mark": flags.get("venue_mark")}
    out = {
        "assets": sorted(by_asset.values(),
                         key=lambda a: -(a.get("core", {}).get("cohort_oi") or 0)),
        "computed_at": cy.isoformat(), "resolution": "20m",
        "cohort": cohort.summary(),
    }
    _cache_put(key, out)
    return out


def _bucketize_entries(rows: list[dict], side: str,
                       wmap: dict[str, float] | None = None) -> list[dict]:
    """Entry buckets with count + notional mass; when `wmap` (Tier-2 C3
    quality weights per wallet) is given, each bucket also carries `quality`
    — the summed normalized wallet weight of its positions."""
    pts = [(r["entry_px"], r["notional"], r["wallet"].lower()) for r in rows
           if r["side"] == side and r["entry_px"]]
    if not pts:
        return []
    lo = min(p for p, _, _ in pts)
    hi = max(p for p, _, _ in pts)
    if hi <= lo:
        b = {"px_lo": lo, "px_hi": hi, "count": len(pts),
             "notional": round(sum(n for _, n, _ in pts), 2)}
        if wmap is not None:
            b["quality"] = round(sum(wmap.get(w, 0.0) for _, _, w in pts), 6)
        return [b]
    step = (hi - lo) / ENTRY_BUCKET_COUNT
    buckets = [{"px_lo": lo + i * step, "px_hi": lo + (i + 1) * step,
                "count": 0, "notional": 0.0} for i in range(ENTRY_BUCKET_COUNT)]
    if wmap is not None:
        for b in buckets:
            b["quality"] = 0.0
    for px, n, w in pts:
        i = min(int((px - lo) / step), ENTRY_BUCKET_COUNT - 1)
        buckets[i]["count"] += 1
        buckets[i]["notional"] = round(buckets[i]["notional"] + n, 2)
        if wmap is not None:
            buckets[i]["quality"] = round(buckets[i]["quality"] + wmap.get(w, 0.0), 6)
    return buckets


AGE_BANDS = {"h24", "d1_7", "d7_plus", "undated"}
PNL_STATES = {"profit", "underwater"}


@router.get("/assets/{asset}")
async def asset_detail(
    asset: str,
    top: int = Query(400, description="cohort top-N slice: 10|50|100|400"),
    include_mm: bool = Query(False),
    include_hedgers: bool = Query(False, description="Tier-2 C1: include wallets flagged as spot-vs-perp hedgers (excluded from CORE by default, like MMs)"),
    min_notional: float = Query(0, ge=0),
    win_rate_min: float | None = Query(None, ge=0, le=1, description="7d win-rate floor (fill stats; NULL win rates excluded when set)"),
    consistent_only: bool = Query(False, description="profitable in >=3 of 4 leaderboard windows"),
    account_band: str | None = Query(None, description="lt_10k|10k_100k|100k_1m|1m_10m|gt_10m"),
    active_within_days: int | None = Query(None, description="7 or 30 — at least one active trading day in the window"),
    lev_band: str | None = Query(None, description="0_2|2_5|5_10|10_plus"),
    age_band: str | None = Query(None, description="h24|d1_7|d7_plus|undated"),
    pnl_state: str | None = Query(None, description="profit|underwater"),
    quality_weights: bool = Query(False, description="Tier-2 C3: attach per-bucket quality mass (win-rate x consistency normalized; wallets without stats weigh 0)"),
) -> dict:
    """Deep-dive payload for one asset. ALL filters applied server-side
    against the latest cycle's raw position rows (spec 1.6 + Phase-3 filter
    matrix) — never against the precomputed rollups. Query cost is bounded:
    one cycle x one asset of position rows (<= cohort size), plus one bulk
    read per ACTIVE wallet-level filter."""
    from app.services.analytics import depth as depth_mod
    t0 = time.monotonic()
    asset = asset.upper()
    # boot correctness: without this, the first asset request after a restart
    # sees an empty rank map and filters every row out (latent since Phase 3;
    # surfaced by Tier-2 C dev verification)
    await cohort.ensure()
    if top not in COHORT_TOPS:
        raise HTTPException(status_code=400, detail=f"top must be one of {sorted(COHORT_TOPS)}")
    if account_band is not None and account_band not in depth_mod.ACCOUNT_BANDS:
        raise HTTPException(status_code=400, detail=f"account_band must be one of {sorted(depth_mod.ACCOUNT_BANDS)}")
    if lev_band is not None and lev_band not in depth_mod.LEV_BANDS:
        raise HTTPException(status_code=400, detail=f"lev_band must be one of {sorted(depth_mod.LEV_BANDS)}")
    if age_band is not None and age_band not in AGE_BANDS:
        raise HTTPException(status_code=400, detail=f"age_band must be one of {sorted(AGE_BANDS)}")
    if pnl_state is not None and pnl_state not in PNL_STATES:
        raise HTTPException(status_code=400, detail=f"pnl_state must be one of {sorted(PNL_STATES)}")
    if active_within_days is not None and active_within_days not in (7, 30):
        raise HTTPException(status_code=400, detail="active_within_days must be 7 or 30")
    cy = await _latest_cycle()
    if cy is None:
        return {"asset": asset, "computed_at": None, "resolution": "20m",
                "positioning": None, "note": "no sweep cycle yet"}
    key = ("asset", asset, top, include_mm, include_hedgers, min_notional,
           win_rate_min, consistent_only, account_band, active_within_days,
           lev_band, age_band, pnl_state, quality_weights, cy.isoformat())
    cached = _cache_get(key)
    if cached is not None:
        return cached

    sf = get_session_factory()
    async with sf() as s:
        prow = (await s.execute(text(
            "SELECT MAX(cycle_ts) FROM analytics_positions"))).scalar()
        pos_cy = prow or cy
        rows = [dict(r) for r in (await s.execute(text(
            "SELECT wallet, side, size, notional, entry_px, leverage, liq_px, "
            " upnl, source FROM analytics_positions "
            "WHERE cycle_ts = :cy AND asset = :a AND side != 'flat' "
            "LIMIT 5000"),
            {"cy": pos_cy, "a": asset})).mappings().all()]
        # 48h rollup series for the trend sparkline (default core variant)
        series = (await s.execute(text(
            "SELECT cycle_ts, notional_long, notional_short, wallets_long, "
            " wallets_short FROM analytics_asset_rollups "
            "WHERE asset = :a AND cohort_variant = 'core' "
            " AND cycle_ts >= :cut ORDER BY cycle_ts"),
            {"a": asset, "cut": cy - timedelta(hours=48)})).mappings().all()
        # stored rollup row (both variants) for the default header numbers
        stored = (await s.execute(text(
            "SELECT cohort_variant, venue_oi, venue_funding, flags "
            "FROM analytics_asset_rollups WHERE cycle_ts = :cy AND asset = :a"),
            {"cy": cy, "a": asset})).mappings().all()

    # ---- wallet-level filter inputs: one bulk read per ACTIVE filter -------
    row_wallets = sorted({r["wallet"].lower() for r in rows})
    wr_map: dict = {}
    if win_rate_min is not None and row_wallets:
        try:
            from app.services.hyperliquid import fill_stats as hl_fill_stats
            wr_map = await hl_fill_stats.get_stats_bulk(row_wallets)
        except Exception:
            wr_map = {}
    act_map: dict[str, dict] = {}
    if (consistent_only or active_within_days is not None) and row_wallets:
        placeholders = ",".join(f":w{i}" for i in range(len(row_wallets)))
        params = {f"w{i}": w for i, w in enumerate(row_wallets)}
        async with sf() as s:
            arows = (await s.execute(text(
                "SELECT wallet_address, active_days_7d, active_days_30d, "
                " BIT_COUNT(COALESCE(consistency_flags, 0)) cbits, "
                " (consistency_flags IS NULL) cnull "
                "FROM trader_activity_metrics WHERE exchange = 'hl' "
                f"AND wallet_address IN ({placeholders})"), params)).all()
        for w, a7, a30, cbits, cnull in arows:
            act_map[w.lower()] = {"a7": a7, "a30": a30,
                                  "cbits": None if cnull else int(cbits)}
    av_map: dict[str, float] = {}
    if account_band is not None and row_wallets:
        av_map = await depth_mod.account_values(row_wallets)

    # dating map fetched BEFORE filtering (age_band filter + age panel share it)
    dmap: dict[tuple[str, int], object] = {}
    if row_wallets:
        placeholders = ",".join(f":w{i}" for i in range(len(row_wallets)))
        params = {f"w{i}": w for i, w in enumerate(row_wallets)}
        params["c"] = asset
        async with sf() as s:
            drows = (await s.execute(text(
                "SELECT wallet, side_sign, opened_at FROM hl_position_open_cache "
                f"WHERE coin = :c AND opened_at IS NOT NULL AND wallet IN ({placeholders})"),
                params)).all()
        dmap = {(w.lower(), int(sg)): oa for w, sg, oa in drows}

    mark = _mid_local(asset)
    if mark is None:
        for srow in stored:
            flags = json.loads(srow["flags"]) if srow["flags"] else {}
            if flags.get("venue_mark"):
                mark = float(flags["venue_mark"])
                break

    def _row_age_band(r: dict) -> str:
        oa = dmap.get((r["wallet"].lower(), 1 if r["side"] == "long" else -1))
        if oa is None:
            return "undated"
        delta = datetime.utcnow() - oa
        if delta <= timedelta(hours=24):
            return "h24"
        if delta <= timedelta(days=7):
            return "d1_7"
        return "d7_plus"

    # ---- server-side filters over raw rows (Phase-3 matrix) ----------------
    mm_excluded = 0
    hedger_excluded = 0
    mm_unknown_included = 0
    matrix_excluded = {"win_rate": 0, "consistency": 0, "account_band": 0,
                      "activity": 0, "leverage": 0, "age": 0, "pnl_state": 0}
    filtered: list[dict] = []
    for r in rows:
        rank = cohort.rank_of(r["wallet"])
        if rank is None or rank > top:
            continue
        if float(r["notional"]) < min_notional:
            continue
        flag = cohort.mm_flag(r["wallet"])
        if not include_mm and flag is True:
            mm_excluded += 1
            continue
        if not include_hedgers and cohort.hedger_flag(r["wallet"]) is True:
            hedger_excluded += 1
            continue
        w = r["wallet"].lower()
        # each active matrix filter EXCLUDES rows whose input is unknown —
        # a filtered view never silently includes what it cannot verify
        if win_rate_min is not None:
            wr = (wr_map.get(w) or {}).get("win_rate_7d")
            if wr is None or float(wr) < win_rate_min:
                matrix_excluded["win_rate"] += 1
                continue
        if consistent_only:
            cbits = (act_map.get(w) or {}).get("cbits")
            if cbits is None or cbits < 3:
                matrix_excluded["consistency"] += 1
                continue
        if active_within_days is not None:
            a = act_map.get(w) or {}
            days = a.get("a7") if active_within_days == 7 else a.get("a30")
            if not days:
                matrix_excluded["activity"] += 1
                continue
        if account_band is not None:
            av = av_map.get(w)
            lo, hi = depth_mod.ACCOUNT_BANDS[account_band]
            if av is None or not (lo <= av < hi):
                matrix_excluded["account_band"] += 1
                continue
        r["notional"] = float(r["notional"])
        r["entry_px"] = float(r["entry_px"]) if r["entry_px"] is not None else None
        r["leverage"] = float(r["leverage"]) if r["leverage"] is not None else None
        r["liq_px"] = float(r["liq_px"]) if r["liq_px"] is not None else None
        r["upnl"] = float(r["upnl"]) if r["upnl"] is not None else None
        r["size"] = float(r["size"])
        if lev_band is not None:
            lo, hi = depth_mod.LEV_BANDS[lev_band]
            if r["leverage"] is None or not (lo <= r["leverage"] < hi):
                matrix_excluded["leverage"] += 1
                continue
        if age_band is not None and _row_age_band(r) != age_band:
            matrix_excluded["age"] += 1
            continue
        if pnl_state is not None:
            in_profit: bool | None = None
            if r["upnl"] is not None:
                in_profit = r["upnl"] > 0
            elif mark and r["entry_px"]:
                in_profit = (mark > r["entry_px"]) if r["side"] == "long" else (mark < r["entry_px"])
            if in_profit is None or in_profit != (pnl_state == "profit"):
                matrix_excluded["pnl_state"] += 1
                continue
        if flag is None:
            mm_unknown_included += 1
        filtered.append(r)

    longs = [r for r in filtered if r["side"] == "long"]
    shorts = [r for r in filtered if r["side"] == "short"]

    # ---- Tier-2 C2: entity dedup — count-weighted views take ONE vote per
    # detected same-entity cluster (UI tooltip: "N wallets deduped as M
    # entities"). Independent wallets are their own entity.
    from app.services.analytics import clustering as clustering_mod
    await clustering_mod.ensure_loaded()
    entities_long = len({clustering_mod.entity_of(r["wallet"]) for r in longs})
    entities_short = len({clustering_mod.entity_of(r["wallet"]) for r in shorts})

    # ---- Tier-2 C3 (+D): quality weights — win_rate x profit-factor x
    # consistency-flag, normalized over the slice. PF's contribution is
    # capped at PF_WEIGHT_CAP so a no-loss 999.99 book cannot drown every
    # other wallet. Wallets without stats (or failing consistency) weigh
    # EXACTLY 0 and the payload states how many were excluded.
    qmap: dict[str, float] | None = None
    quality_info = None
    if quality_weights:
        fw = sorted({r["wallet"].lower() for r in filtered})
        stats_map: dict = {}
        cons_map: dict[str, int | None] = {}
        if fw:
            try:
                from app.services.hyperliquid import fill_stats as hl_fill_stats
                stats_map = await hl_fill_stats.get_stats_bulk(fw)
            except Exception:
                stats_map = {}
            placeholders = ",".join(f":w{i}" for i in range(len(fw)))
            params = {f"w{i}": w for i, w in enumerate(fw)}
            async with sf() as s:
                crows = (await s.execute(text(
                    "SELECT wallet_address, "
                    " BIT_COUNT(COALESCE(consistency_flags, 0)) cbits, "
                    " (consistency_flags IS NULL) cnull "
                    "FROM trader_activity_metrics WHERE exchange = 'hl' "
                    f"AND wallet_address IN ({placeholders})"), params)).all()
            for w, cbits, cnull in crows:
                cons_map[w.lower()] = None if cnull else int(cbits)
        PF_WEIGHT_CAP = 3.0
        raw_w: dict[str, float] = {}
        for w in fw:
            fs = stats_map.get(w) or {}
            wr = fs.get("win_rate_7d")
            pf = fs.get("profit_factor")
            cb = cons_map.get(w)
            consistent = cb is not None and cb >= 3
            if wr is None or pf is None or not consistent:
                raw_w[w] = 0.0
            else:
                raw_w[w] = float(wr) * min(float(pf), PF_WEIGHT_CAP)
        total_qw = sum(raw_w.values())
        qmap = {w: (v / total_qw if total_qw > 0 else 0.0) for w, v in raw_w.items()}
        quality_info = {
            "weighted_wallets": sum(1 for v in raw_w.values() if v > 0),
            "excluded_wallets": sum(1 for v in raw_w.values() if v <= 0),
            "quality_long": round(sum(qmap.get(r["wallet"].lower(), 0.0) for r in longs), 6),
            "quality_short": round(sum(qmap.get(r["wallet"].lower(), 0.0) for r in shorts), 6),
            "beta": "win_rate_pf_consistency",
        }

    def lev_hist(rs: list[dict]) -> list[dict]:
        known = [r["leverage"] for r in rs if r["leverage"]]
        out = []
        for lo, hi in LEV_BUCKETS:
            n = len([v for v in known if lo <= v < hi])
            if n:
                out.append({"lo": lo, "hi": hi, "count": n})
        return out

    # age buckets from the pre-fetched dating map (undated = honest bucket)
    age = {"h24": 0, "d1_7": 0, "d7_plus": 0, "undated": 0}
    for r in filtered:
        age[_row_age_band(r)] += 1

    # liq buckets (Design Guide B3.1 + owner review): 3 buckets per side within
    # the NEAR band mark ±LIQ_NEAR_PCT; everything beyond goes into ONE labeled
    # far row per side instead of stretching the near buckets.
    liq = {"below_mark": [], "above_mark": []}
    if mark:
        known = [(r["liq_px"], r["notional"]) for r in filtered if r["liq_px"]]
        below = [(p, n) for p, n in known if p < mark]
        above = [(p, n) for p, n in known if p >= mark]
        for name, group in (("below_mark", below), ("above_mark", above)):
            if not group:
                continue
            if name == "above_mark":
                near = [(p, n) for p, n in group if p <= mark * (1 + LIQ_NEAR_PCT)]
                far = [(p, n) for p, n in group if p > mark * (1 + LIQ_NEAR_PCT)]
            else:
                near = [(p, n) for p, n in group if p >= mark * (1 - LIQ_NEAR_PCT)]
                far = [(p, n) for p, n in group if p < mark * (1 - LIQ_NEAR_PCT)]
            bs = []
            if near:
                los = min(p for p, _ in near)
                his = max(p for p, _ in near)
                step = (his - los) / LIQ_BUCKETS_PER_SIDE if his > los else 1
                bs = [{"px_lo": los + i * step, "px_hi": los + (i + 1) * step,
                       "count": 0, "notional": 0.0, "far": False}
                      for i in range(LIQ_BUCKETS_PER_SIDE)]
                for p, n in near:
                    i = (min(int((p - los) / step), LIQ_BUCKETS_PER_SIDE - 1)
                         if his > los else 0)
                    bs[i]["count"] += 1
                    bs[i]["notional"] = round(bs[i]["notional"] + n, 2)
            if far:
                bs.append({"px_lo": min(p for p, _ in far),
                           "px_hi": max(p for p, _ in far),
                           "count": len(far),
                           "notional": round(sum(n for _, n in far), 2),
                           "far": True})
            liq[name] = [b for b in bs if b["count"]]

    nl = sum(r["notional"] for r in longs)
    ns = sum(r["notional"] for r in shorts)
    longs_in_profit = None
    if mark and longs:
        with_entry = [r for r in longs if r["entry_px"]]
        if with_entry:
            longs_in_profit = round(
                100.0 * len([r for r in with_entry if mark > r["entry_px"]]) / len(with_entry), 1)

    venue = {}
    for srow in stored:
        flags = json.loads(srow["flags"]) if srow["flags"] else {}
        venue = {"oi_usd": float(srow["venue_oi"]) if srow["venue_oi"] is not None else None,
                 "funding_hourly": float(srow["venue_funding"]) if srow["venue_funding"] is not None else None,
                 "mark": mark or flags.get("venue_mark")}
        break

    # ---- Phase-3: crowding on the FILTERED slice, conviction on members ----
    clusters = depth_mod.crowding_clusters(filtered)
    if clusters:
        member_wallets = sorted({m["wallet"].lower()
                                 for c in clusters for m in c["wallets"]})
        avs = await depth_mod.account_values(member_wallets)
        for c in clusters:
            for m in c["wallets"]:
                m["conviction"] = depth_mod.conviction(
                    m["notional"], m.get("leverage"), avs.get(m["wallet"].lower()))
                m["rank"] = cohort.rank_of(m["wallet"])
            # C2: crowd score deduped — one vote per same-entity cluster
            c["entity_count"] = len({clustering_mod.entity_of(m["wallet"])
                                     for m in c["wallets"]})

    # ---- Phase-3: trigger clusters (observed resting TP/SL; sparse default) --
    trig = await depth_mod.trigger_snapshot(asset)
    trig_claimed = len(trig["triggers"]) >= trig["min_claim"]
    trigger_buckets = {"below_mark": [], "above_mark": []}
    if trig_claimed and mark:
        tpts = [(t["trigger_px"], t["size"] or 0.0) for t in trig["triggers"]]
        below = [(p, n) for p, n in tpts if p < mark]
        above = [(p, n) for p, n in tpts if p >= mark]
        for name, group in (("below_mark", below), ("above_mark", above)):
            if not group:
                continue
            los = min(p for p, _ in group)
            his = max(p for p, _ in group)
            step = (his - los) / 6 if his > los else 1
            bs = [{"px_lo": los + i * step, "px_hi": los + (i + 1) * step,
                   "count": 0} for i in range(6)]
            for p, _n in group:
                i = min(int((p - los) / step), 5) if his > los else 0
                bs[i]["count"] += 1
            trigger_buckets[name] = [b for b in bs if b["count"]]

    out = {
        "asset": asset,
        "computed_at": pos_cy.isoformat(), "resolution": "20m",
        "filters": {"top": top, "include_mm": include_mm,
                    "include_hedgers": include_hedgers,
                    "min_notional": min_notional,
                    "mm_excluded": mm_excluded,
                    "hedger_excluded": hedger_excluded,
                    "mm_unknown_included": mm_unknown_included,
                    "win_rate_min": win_rate_min,
                    "consistent_only": consistent_only,
                    "account_band": account_band,
                    "active_within_days": active_within_days,
                    "lev_band": lev_band, "age_band": age_band,
                    "pnl_state": pnl_state,
                    "matrix_excluded": matrix_excluded},
        "low_sample": len(filtered) < LOW_SAMPLE_MIN,
        "sample_wallets": len({r["wallet"] for r in filtered}),
        "positioning": {
            "wallets_long": len({r["wallet"] for r in longs}),
            "wallets_short": len({r["wallet"] for r in shorts}),
            "entities_long": entities_long,
            "entities_short": entities_short,
            "notional_long": round(nl, 2), "notional_short": round(ns, 2),
            "net_notional": round(nl - ns, 2),
            "avg_lev_long": round(sum(r["leverage"] for r in longs if r["leverage"]) /
                                  max(1, len([r for r in longs if r["leverage"]])), 2)
                            if any(r["leverage"] for r in longs) else None,
            "avg_lev_short": round(sum(r["leverage"] for r in shorts if r["leverage"]) /
                                   max(1, len([r for r in shorts if r["leverage"]])), 2)
                             if any(r["leverage"] for r in shorts) else None,
            "upnl_long": round(sum(r["upnl"] or 0 for r in longs), 2),
            "upnl_short": round(sum(r["upnl"] or 0 for r in shorts), 2),
            "cohort_oi": round(nl + ns, 2),
            "longs_in_profit_pct": longs_in_profit,
        },
        "entry_distribution": {
            "long": _bucketize_entries(filtered, "long", qmap),
            "short": _bucketize_entries(filtered, "short", qmap),
            "mark": mark,
        },
        "quality_weighting": quality_info,
        "leverage_histogram": {"long": lev_hist(longs), "short": lev_hist(shorts)},
        "age_buckets": age,
        "liq_buckets": liq,
        "crowding": {
            "clusters": clusters[:10],
            "min_wallets": depth_mod.CROWDING_MIN_WALLETS,
            "entry_tol_pct": depth_mod.ENTRY_TOL * 100,
        },
        "trigger_clusters": {
            "claimed": trig_claimed,
            "observed": len(trig["triggers"]),
            "wallets_checked": trig["wallets_checked"],
            "wallets_with_triggers": trig["wallets_with_triggers"],
            "min_claim": trig["min_claim"],
            "window_days": trig["window_days"],
            "buckets": trigger_buckets,
        },
        "venue": venue,
        "trend_48h": [
            {"t": r["cycle_ts"].isoformat(),
             "net_notional": round(float(r["notional_long"]) - float(r["notional_short"]), 2),
             "wallets_long": r["wallets_long"], "wallets_short": r["wallets_short"]}
            for r in series
        ],
    }
    depth_mod.record_latency("asset_detail", (time.monotonic() - t0) * 1000)
    # Boot-race guard: raw rows exist but the whole slice filtered out with no
    # narrowing filters active — that shape only happens transiently while the
    # cohort is (re)hydrating, and caching it would freeze an empty page for a
    # full cycle. Serve it, don't cache it.
    suspicious_empty = (bool(rows) and not filtered and top == 400
                        and not min_notional and win_rate_min is None
                        and not consistent_only and account_band is None
                        and active_within_days is None and lev_band is None
                        and age_band is None and pnl_state is None)
    if not suspicious_empty:
        _cache_put(key, out)
    return out


MAJORS = ("BTC", "ETH")           # risk-rotation gauge: majors vs everything else
DIVERGENCE_MIN_SKEW = 0.15        # |2s-1| must exceed this to call a direction
DIVERGENCE_MIN_FUNDING = 0.00001  # 0.1bp/h — funding smaller than this is noise
PRICE_MOVE_MIN_PCT = 1.0          # 24h price move against skew must exceed this


@router.get("/smi/{asset}")
async def smi_detail(asset: str, hours: int = Query(168, ge=24, le=2160)) -> dict:
    """SMI history + latest sub-scores with raw inputs (no black boxes —
    every number audits down to services/analytics/smi.py formulas)."""
    from app.services.analytics import smi as smi_mod
    asset = asset.upper()
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT cycle_ts, smi, c1, c2, c3, c4, c5, inputs FROM analytics_smi "
            "WHERE asset = :a AND cycle_ts >= :cut ORDER BY cycle_ts"),
            {"a": asset, "cut": datetime.utcnow() - timedelta(hours=hours)}
        )).mappings().all()
    hist_h = await smi_mod.history_hours()
    latest = rows[-1] if rows else None
    # Tier-2 Part A4: the track-record payload comes from smi_study.track_record
    # ONLY — the publication threshold (n>=30 per bucket/horizon AND span>=21d)
    # is enforced inside that function, server-side. Below threshold the API
    # returns the collecting state and NO study numbers at all.
    from app.services.analytics import smi_study
    try:
        track = await smi_study.track_record(asset)
    except Exception:
        track = None
    return {
        "asset": asset,
        "calibrating": hist_h < smi_mod.CALIBRATION_HOURS,
        "track_record": track,
        "history_hours": round(hist_h, 1),
        "weights": {"skew": smi_mod.W_SKEW, "flow": smi_mod.W_FLOW,
                    "breadth": smi_mod.W_BREADTH, "leverage": smi_mod.W_LEV,
                    "divergence": smi_mod.W_DIV},
        "latest": ({
            "cycle_ts": latest["cycle_ts"].isoformat(), "smi": float(latest["smi"]),
            "components": {k: float(latest[k]) for k in ("c1", "c2", "c3", "c4", "c5")},
            "inputs": json.loads(latest["inputs"]) if latest["inputs"] else {},
        } if latest else None),
        "series": [{"t": r["cycle_ts"].isoformat(), "smi": float(r["smi"])} for r in rows],
        "computed_at": latest["cycle_ts"].isoformat() if latest else None,
        "resolution": "20m",
    }



# ---- D-113: cached BIG-pill threshold -------------------------------------
# The "outsized event" threshold is the p95 of |notional_delta| over the
# trailing 7 days. Computing it costs a COUNT(*) plus an
# `ORDER BY ABS(notional_delta) ... OFFSET n` over ~1M rows — measured at
# 8.4 s + 6.8 s = 15.2 s of the 15.24 s a /flows request took, and /pulse paid
# it too. It is a 7-day percentile: it does not move meaningfully minute to
# minute, so recomputing it per request was pure waste. Cached for 10 minutes;
# a miss falls back to computing it exactly as before.
_P95_TTL_S = 600.0
_p95_cache: dict[str, tuple[float, float | None]] = {}


async def _big_threshold(s) -> float | None:
    """p95 of |notional_delta| over the trailing 7d, cached for 10 minutes."""
    import time as _t
    hit = _p95_cache.get("p95")
    if hit and (_t.monotonic() - hit[0]) < _P95_TTL_S:
        return hit[1]
    cut = datetime.utcnow() - timedelta(days=7)
    cnt = (await s.execute(text(
        "SELECT COUNT(*) FROM analytics_flow_events "
        "WHERE detected_at >= :cut AND notional_delta IS NOT NULL"),
        {"cut": cut})).scalar() or 0
    val = None
    if cnt:
        val = (await s.execute(text(
            "SELECT ABS(notional_delta) FROM analytics_flow_events "
            "WHERE detected_at >= :cut AND notional_delta IS NOT NULL "
            "ORDER BY ABS(notional_delta) DESC LIMIT 1 OFFSET :off"),
            {"cut": cut, "off": max(0, int(cnt * 0.05))})).scalar()
    val = float(val) if val is not None else None
    _p95_cache["p95"] = (_t.monotonic(), val)
    return val


# ---- D-113: background pulse warmer ---------------------------------------
# /pulse caches on the sweep cycle, so the FIRST request after each new cycle
# (every ~26-31 min) paid the full rebuild — measured at 23.6 s, on the single
# uvicorn worker, blocking every other request behind it. That is what users
# experienced as "analytics is slow": not every load, just the unlucky one.
# This task rebuilds the cache as soon as a new cycle lands, so a user request
# never does. Read-only; it calls the same endpoint function.
async def warm_pulse_loop(stop_evt) -> None:
    import asyncio as _a
    last_seen = None
    while not stop_evt.is_set():
        try:
            cy = await _latest_cycle()
            if cy is not None and cy != last_seen:
                t0 = time.monotonic()
                await pulse()
                last_seen = cy
                logger.info("pulse cache warmed for cycle %s in %.2fs",
                            cy.isoformat(), time.monotonic() - t0)
        except Exception as exc:          # noqa: BLE001 — a warmer must never die
            logger.warning("pulse warm failed: %s", exc)
        try:
            await _a.wait_for(stop_evt.wait(), timeout=60)
            return
        except _a.TimeoutError:
            pass

@router.get("/pulse")
async def pulse() -> dict:
    """Market Pulse landing payload (Phase 2.2): heatmap tiles, SMI movers,
    risk-rotation gauge, flow highlights, divergence callouts. DB-only."""
    from app.services.analytics import smi as smi_mod
    _t0_pulse = time.monotonic()
    await cohort.ensure()
    cy = await _latest_cycle()
    if cy is None:
        return {"tiles": [], "movers": [], "computed_at": None, "resolution": "20m",
                "cohort": cohort.summary()}
    key = ("pulse", cy.isoformat())
    cached = _cache_get(key)
    if cached is not None:
        return cached

    now = datetime.utcnow()
    sf = get_session_factory()
    async with sf() as s:
        latest_smi = (await s.execute(text(
            "SELECT m.asset, m.smi, m.inputs FROM analytics_smi m JOIN ("
            "  SELECT asset, MAX(cycle_ts) mt FROM analytics_smi GROUP BY asset"
            ") l ON l.asset = m.asset AND l.mt = m.cycle_ts"))).mappings().all()
        smi_24h = (await s.execute(text(
            "SELECT m.asset, m.smi FROM analytics_smi m JOIN ("
            "  SELECT asset, MIN(cycle_ts) mt FROM analytics_smi "
            "  WHERE cycle_ts >= :cut GROUP BY asset"
            ") f ON f.asset = m.asset AND f.mt = m.cycle_ts"),
            {"cut": now - timedelta(hours=24)})).all()
        rollup_now = (await s.execute(text(
            "SELECT asset, cohort_oi, notional_long, notional_short, venue_funding, flags "
            "FROM analytics_asset_rollups WHERE cycle_ts = :cy AND cohort_variant = 'core'"),
            {"cy": cy})).mappings().all()
        # majors-vs-alts notional share per cycle over 7d (risk rotation trend)
        from sqlalchemy import bindparam
        rotation = (await s.execute(text(
            "SELECT cycle_ts, "
            " SUM(CASE WHEN asset IN :majors THEN cohort_oi ELSE 0 END) majors_oi, "
            " SUM(cohort_oi) total_oi "
            "FROM analytics_asset_rollups "
            "WHERE cohort_variant = 'core' AND cycle_ts >= :cut "
            "GROUP BY cycle_ts ORDER BY cycle_ts"
        ).bindparams(bindparam("majors", expanding=True)),
            {"majors": list(MAJORS), "cut": now - timedelta(days=7)})).all()
        # biggest single flow event today (UTC day)
        biggest = (await s.execute(text(
            "SELECT detected_at, wallet, asset, event_type, side, notional_delta, "
            " ref_px, ref_px_approx, resolution FROM analytics_flow_events "
            "WHERE detected_at >= :day AND notional_delta IS NOT NULL "
            "ORDER BY ABS(notional_delta) DESC LIMIT 1"),
            {"day": now.replace(hour=0, minute=0, second=0, microsecond=0)})).mappings().first()
        # p95 of |delta| over trailing 7d — outsized-event threshold (D-113: cached)
        p95 = await _big_threshold(s)
        highlights = (await s.execute(text(
            "SELECT detected_at, wallet, asset, event_type, side, notional_delta, "
            " ref_px, ref_px_approx, resolution FROM analytics_flow_events "
            "WHERE detected_at >= :cut AND (event_type = 'FLIP' "
            " OR ABS(COALESCE(notional_delta, 0)) >= :p95) "
            "ORDER BY detected_at DESC LIMIT 40"),
            {"cut": now - timedelta(hours=24), "p95": float(p95 or 0) or 1e18}
        )).mappings().all()
        # 24h-ago venue mark per asset (price-vs-skew divergence input)
        old_marks = (await s.execute(text(
            "SELECT r.asset, r.flags FROM analytics_asset_rollups r JOIN ("
            "  SELECT asset, MIN(cycle_ts) mt FROM analytics_asset_rollups "
            "  WHERE cohort_variant = 'core' AND cycle_ts >= :cut GROUP BY asset"
            ") o ON o.asset = r.asset AND o.mt = r.cycle_ts "
            "WHERE r.cohort_variant = 'core'"),
            {"cut": now - timedelta(hours=24)})).all()
        # Phase-3 crowding sweep: latest cycle's raw rows, all assets
        pos_cy = (await s.execute(text(
            "SELECT MAX(cycle_ts) FROM analytics_positions"))).scalar()
        crowd_rows = []
        if pos_cy is not None:
            crowd_rows = (await s.execute(text(
                "SELECT wallet, asset, side, notional, entry_px "
                "FROM analytics_positions WHERE cycle_ts = :cy "
                "AND side != 'flat' AND entry_px IS NOT NULL"),
                {"cy": pos_cy})).mappings().all()

    smi_by_asset = {r["asset"]: r for r in latest_smi}
    prev_smi = {a: float(v) for a, v in smi_24h}
    old_mark_by_asset = {}
    for a, fl in old_marks:
        try:
            m = json.loads(fl).get("venue_mark") if fl else None
            if m:
                old_mark_by_asset[a] = float(m)
        except Exception:
            pass

    hist_h = await smi_mod.history_hours()
    calibrating = hist_h < smi_mod.CALIBRATION_HOURS

    tiles = []
    divergences = []
    for r in rollup_now:
        a = r["asset"]
        srow = smi_by_asset.get(a)
        if srow is None:
            continue
        inputs = json.loads(srow["inputs"]) if srow["inputs"] else {}
        nl, ns = float(r["notional_long"]), float(r["notional_short"])
        total = nl + ns
        s_share = nl / total if total else 0.5
        x = 2 * s_share - 1
        net_flow = inputs.get("net_flow_24h") or 0.0
        smi_val = float(srow["smi"])
        stance = ("net_long" if x > STANCE_DEAD_ZONE else
                  "net_short" if x < -STANCE_DEAD_ZONE else "balanced")
        tiles.append({
            "asset": a, "smi": smi_val, "smi_dev": round(smi_val - 50.0, 2),
            "smi_delta_24h": round(smi_val - prev_smi[a], 2) if a in prev_smi else None,
            "cohort_oi": float(r["cohort_oi"]),
            "net_notional": round(nl - ns, 2),
            "stance": stance,
            "flow_dir_24h": 1 if net_flow > 0 else (-1 if net_flow < 0 else 0),
            "net_flow_24h": round(net_flow, 2),
            "wallets": (inputs.get("wallets_long") or 0) + (inputs.get("wallets_short") or 0),
            "low_sample": ((inputs.get("wallets_long") or 0) + (inputs.get("wallets_short") or 0)) < LOW_SAMPLE_MIN,
        })
        # divergence panel entries (formula-tooltipped in UI). Low-sample
        # assets are excluded — a 3-wallet "divergence" is noise, not signal
        # (dev evidence: without this, typical ~1bp positive funding qualified
        # nearly every thin net-short asset).
        funding = float(r["venue_funding"]) if r["venue_funding"] is not None else None
        flags = json.loads(r["flags"]) if r["flags"] else {}
        mark_now = flags.get("venue_mark")
        n_wallets = (inputs.get("wallets_long") or 0) + (inputs.get("wallets_short") or 0)
        if abs(x) >= DIVERGENCE_MIN_SKEW and total > 0 and n_wallets >= LOW_SAMPLE_MIN:
            side = "long" if x > 0 else "short"
            if funding is not None and abs(funding) >= DIVERGENCE_MIN_FUNDING and (x > 0) == (funding < 0):
                divergences.append({
                    "asset": a, "kind": "funding",
                    "cohort_side": side, "skew": round(x, 3), "funding_hourly": funding,
                    "label": f"Cohort net {side} while funding pays the {side}s — contrarian conviction.",
                })
            if mark_now and a in old_mark_by_asset:
                chg = (float(mark_now) - old_mark_by_asset[a]) / old_mark_by_asset[a] * 100
                if abs(chg) >= PRICE_MOVE_MIN_PCT and (x > 0) != (chg > 0):
                    divergences.append({
                        "asset": a, "kind": "price",
                        "cohort_side": side, "skew": round(x, 3), "price_change_24h": round(chg, 2),
                        "label": (f"Price {chg:+.1f}% in 24h against a net-{side} cohort — "
                                  + ("accumulation into weakness." if side == "long" else "distribution into strength.")),
                    })

    tiles.sort(key=lambda t: -t["cohort_oi"])
    movers = sorted((t for t in tiles if t["smi_delta_24h"] is not None),
                    key=lambda t: -abs(t["smi_delta_24h"]))[:3]

    # Phase-3 crowding detector (pulse view): top crowded trades across
    # assets, core-cohort rows (MM-flagged wallets excluded, same as rollups)
    from app.services.analytics import depth as depth_mod
    by_asset_rows: dict[str, list[dict]] = {}
    for r in crowd_rows:
        if (cohort.mm_flag(r["wallet"]) is True
                or cohort.hedger_flag(r["wallet"]) is True):
            continue
        by_asset_rows.setdefault(r["asset"], []).append({
            "wallet": r["wallet"], "side": r["side"],
            "notional": float(r["notional"]),
            "entry_px": float(r["entry_px"]),
        })
    crowded = []
    for a, ars in by_asset_rows.items():
        for c in depth_mod.crowding_clusters(ars):
            crowded.append({"asset": a, "side": c["side"],
                            "entry_lo": c["entry_lo"], "entry_hi": c["entry_hi"],
                            "wallet_count": c["wallet_count"],
                            "notional": c["notional"]})
    crowded.sort(key=lambda c: (-c["wallet_count"], -c["notional"]))
    crowded = crowded[:5]
    # strongest signals first, capped — strength = |skew| × |driver|
    divergences.sort(key=lambda dv: -(abs(dv["skew"]) * abs(
        dv.get("funding_hourly") or dv.get("price_change_24h") or 0)))
    divergences = divergences[:10]

    rot_series = [{"t": ts.isoformat(),
                   "majors_share": round(float(m) / float(tot) * 100, 2) if tot else None}
                  for ts, m, tot in rotation]

    ev_wallets = sorted({e["wallet"].lower() for e in highlights}
                        | ({biggest["wallet"].lower()} if biggest else set()))
    ev_names: dict = {}
    if ev_wallets:
        try:
            from app.services.copy import trader_profiles
            profs = await trader_profiles.get_profiles_bulk(ev_wallets, exchange="hl")
            ev_names = {w: (p or {}).get("display_name") for w, p in profs.items()}
        except Exception:
            ev_names = {}

    def _ev(e) -> dict:
        nd = float(e["notional_delta"]) if e["notional_delta"] is not None else None
        return {"detected_at": e["detected_at"].isoformat(), "wallet": e["wallet"],
                "asset": e["asset"], "event_type": e["event_type"], "side": e["side"],
                "notional_delta": nd,
                "ref_px": float(e["ref_px"]) if e["ref_px"] is not None else None,
                "ref_px_approx": bool(e["ref_px_approx"]), "resolution": e["resolution"],
                "wallet_rank": cohort.rank_of(e["wallet"]),
                "display_name": ev_names.get(e["wallet"].lower()),
                "outsized": bool(p95 and nd is not None and abs(nd) >= float(p95))}

    out = {
        "computed_at": cy.isoformat(), "resolution": "20m",
        "cohort": cohort.summary(),
        "calibrating": calibrating, "history_hours": round(hist_h, 1),
        "tiles": tiles,
        "movers": movers,
        "risk_rotation": {"majors": list(MAJORS), "series": rot_series,
                          "now": rot_series[-1]["majors_share"] if rot_series else None},
        "biggest_event_today": _ev(biggest) if biggest else None,
        "outsized_threshold_p95": round(float(p95), 2) if p95 else None,
        "highlights": [_ev(e) for e in highlights],
        "divergences": divergences,
        "crowded_trades": {"clusters": crowded,
                           "min_wallets": depth_mod.CROWDING_MIN_WALLETS,
                           "entry_tol_pct": depth_mod.ENTRY_TOL * 100},
    }
    depth_mod.record_latency("pulse", (time.monotonic() - _t0_pulse) * 1000)
    _cache_put(key, out)
    return out


@router.get("/flows")
async def flows(
    asset: str | None = Query(None),
    type: str | None = Query(None, description="OPEN|CLOSE|INCREASE|REDUCE|FLIP"),
    window: str = Query("24h", description="1h|4h|24h|7d"),
    min_notional: float = Query(0, ge=0),
    include_mm: bool = Query(False, description="include MM-flagged wallets (owner review: the tape respects the page's MM filter)"),
    page: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """The flow tape — paged, filters server-side. Attaches cohort rank +
    win-rate badge data (existing stats, one bulk read) + conviction.
    Post-fetch steps (owner review 2026-08-26): MM-flagged wallets excluded
    unless include_mm; same-wallet ws INCREASE bursts merged at READ time
    (stored event rows are never merged — same principle as the tracker's
    telegram alert merge, whose window this reuses). Because both steps
    change row counts, pagination slices AFTER post-processing over an
    over-fetched window (cap 1000 rows)."""
    _t0_flows = time.monotonic()
    await cohort.ensure()
    hours = {"1h": 1, "4h": 4, "24h": 24, "7d": 168}.get(window)
    if hours is None:
        raise HTTPException(status_code=400, detail="window must be 1h|4h|24h|7d")
    if type is not None and type.upper() not in ("OPEN", "CLOSE", "INCREASE", "REDUCE", "FLIP"):
        raise HTTPException(status_code=400, detail="bad type")

    where = ["detected_at >= :cut"]
    params: dict = {"cut": datetime.utcnow() - timedelta(hours=hours),
                    "lim": min(1000, (page + 1) * limit * 3)}
    if asset:
        where.append("asset = :a")
        params["a"] = asset.upper()
    if type:
        where.append("event_type = :t")
        params["t"] = type.upper()
    if min_notional > 0:
        where.append("ABS(COALESCE(notional_delta, 0)) >= :mn")
        params["mn"] = min_notional

    sf = get_session_factory()
    async with sf() as s:
        raw_rows = (await s.execute(text(
            "SELECT id, detected_at, wallet, asset, event_type, side, "
            " size_before, size_after, notional_delta, ref_px, ref_px_approx, "
            " resolution FROM analytics_flow_events "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY detected_at DESC, id DESC LIMIT :lim"),
            params)).mappings().all()

    # MM filter — the tape follows the page's MM toggle
    mm_excluded = 0
    filtered_rows = []
    for r in raw_rows:
        if not include_mm and cohort.mm_flag(r["wallet"]) is True:
            mm_excluded += 1
            continue
        filtered_rows.append(dict(r))

    # ws INCREASE burst-merge: runs keyed (wallet, asset, side) whose
    # consecutive gaps stay within the telegram merge window collapse into
    # one display row — summed delta, |delta|-weighted ref price (marked ~),
    # newest timestamp/size_after, oldest size_before, merged_count.
    from app.services.hyperliquid.tracker import ALERT_MERGE_WINDOW_SEC
    merged: list[dict] = []
    bursts: dict[tuple, dict] = {}
    for r in filtered_rows:                      # DESC — newest first
        if r["resolution"] == "ws" and r["event_type"] == "INCREASE":
            key = (r["wallet"].lower(), r["asset"], r["side"])
            b = bursts.get(key)
            if b is not None and (b["_oldest"] - r["detected_at"]).total_seconds() <= ALERT_MERGE_WINDOW_SEC:
                b["_oldest"] = r["detected_at"]
                b["merged_count"] += 1
                if r["notional_delta"] is not None:
                    b["notional_delta"] = (b["notional_delta"] or 0) + float(r["notional_delta"])
                    if r["ref_px"] is not None:
                        b["_wsum"] += float(r["ref_px"]) * abs(float(r["notional_delta"]))
                        b["_w"] += abs(float(r["notional_delta"]))
                if r["size_before"] is not None:
                    b["size_before"] = r["size_before"]   # oldest fill's start
                if b["_w"] > 0:
                    b["ref_px"] = b["_wsum"] / b["_w"]
                    b["ref_px_approx"] = True             # a blended price is approximate
                continue
            r["merged_count"] = 1
            r["_oldest"] = r["detected_at"]
            nd = abs(float(r["notional_delta"])) if r["notional_delta"] is not None else 0.0
            r["_wsum"] = (float(r["ref_px"]) * nd) if r["ref_px"] is not None else 0.0
            r["_w"] = nd if r["ref_px"] is not None else 0.0
            bursts[key] = r
            merged.append(r)
        else:
            r["merged_count"] = 1
            merged.append(r)
    rows = merged[page * limit:(page + 1) * limit]

    wallets = sorted({r["wallet"].lower() for r in rows})
    fstats: dict = {}
    names: dict = {}
    if wallets:
        try:
            from app.services.hyperliquid import fill_stats as hl_fill_stats
            fstats = await hl_fill_stats.get_stats_bulk(wallets)
        except Exception:
            fstats = {}
        try:
            from app.services.copy import trader_profiles
            profs = await trader_profiles.get_profiles_bulk(wallets, exchange="hl")
            names = {w: (p or {}).get("display_name") for w, p in profs.items()}
        except Exception:
            names = {}

    # BIG pill threshold (A3): p95 of |notional_delta| over trailing 7d (D-113: cached)
    async with sf() as s:
        p95 = await _big_threshold(s)

    # Phase-3 conviction meter: current position notional / account value for
    # the event's (wallet, asset), from the latest sweep cycle. Wallets with
    # no persisted account value get NO conviction figure (never invented).
    from app.services.analytics import depth as depth_mod
    conv_map: dict[tuple[str, str], dict] = {}
    if wallets:
        avs = await depth_mod.account_values(wallets)
        if avs:
            pairs = sorted({(r["wallet"].lower(), r["asset"]) for r in rows})
            wl = sorted({w for w, _ in pairs})
            placeholders = ",".join(f":w{i}" for i in range(len(wl)))
            params = {f"w{i}": w for i, w in enumerate(wl)}
            async with sf() as s:
                pcy = (await s.execute(text(
                    "SELECT MAX(cycle_ts) FROM analytics_positions"))).scalar()
                if pcy is not None:
                    params["cy"] = pcy
                    prow = (await s.execute(text(
                        "SELECT wallet, asset, notional, leverage FROM analytics_positions "
                        f"WHERE cycle_ts = :cy AND wallet IN ({placeholders}) "
                        "AND side != 'flat'"), params)).all()
                    for w, a, n, lev in prow:
                        cv = depth_mod.conviction(
                            float(n), float(lev) if lev is not None else None,
                            avs.get(w.lower()))
                        if cv is not None:
                            conv_map[(w.lower(), a)] = cv

    depth_mod.record_latency("flows", (time.monotonic() - _t0_flows) * 1000)
    return {
        "events": [{
            "id": r["id"], "detected_at": r["detected_at"].isoformat(),
            "wallet": r["wallet"], "asset": r["asset"],
            "event_type": r["event_type"], "side": r["side"],
            "size_before": float(r["size_before"]) if r["size_before"] is not None else None,
            "size_after": float(r["size_after"]) if r["size_after"] is not None else None,
            "notional_delta": float(r["notional_delta"]) if r["notional_delta"] is not None else None,
            "ref_px": float(r["ref_px"]) if r["ref_px"] is not None else None,
            "ref_px_approx": bool(r["ref_px_approx"]),
            "resolution": r["resolution"],
            "wallet_rank": cohort.rank_of(r["wallet"]),
            "display_name": names.get(r["wallet"].lower()),
            "win_rate_7d": (fstats.get(r["wallet"].lower()) or {}).get("win_rate_7d"),
            "profit_factor": (fstats.get(r["wallet"].lower()) or {}).get("profit_factor"),
            "conviction": conv_map.get((r["wallet"].lower(), r["asset"])),
            "is_mm": cohort.mm_flag(r["wallet"]) is True,
            "outsized": bool(p95 and r["notional_delta"] is not None
                             and abs(float(r["notional_delta"])) >= float(p95)),
            "merged_count": r.get("merged_count", 1),
        } for r in rows],
        "include_mm": include_mm, "mm_excluded": mm_excluded,
        "page": page, "limit": limit, "window": window,
        "computed_at": datetime.utcnow().isoformat(),
        "resolution": "mixed",
    }


MOVER_MAX = 10               # Design Guide B4: mover cards max 10
ENTRANT_EXIT_MAX = 10


@router.get("/movers")
async def movers(
    window: str = Query("24h", description="1h|4h|24h|7d"),
    asset: str | None = Query(None),
) -> dict:
    """"Who moved" page payload (Design Guide B4). Aggregates the flow-event
    stream per (wallet, asset) over the window: top movers by |net notional
    change|, plus New entrants (OPEN events) and Full exits (CLOSE to zero).
    DB-only; conviction/win-rate/names attached from existing stores.
    Move-kind rule (documented): FLIP if any FLIP event in the window; else
    OPEN present -> 'opened'; else CLOSE present -> 'closed'; else net>0 ->
    'grew', net<0 -> 'cut'."""
    from app.services.analytics import depth as depth_mod
    _t0 = time.monotonic()
    hours = {"1h": 1, "4h": 4, "24h": 24, "7d": 168}.get(window)
    if hours is None:
        raise HTTPException(status_code=400, detail="window must be 1h|4h|24h|7d")
    await cohort.ensure()
    cut = datetime.utcnow() - timedelta(hours=hours)

    where = ["detected_at >= :cut"]
    params: dict = {"cut": cut}
    if asset:
        where.append("asset = :a")
        params["a"] = asset.upper()

    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT wallet, asset, event_type, side, size_after, "
            " notional_delta, detected_at FROM analytics_flow_events "
            f"WHERE {' AND '.join(where)} ORDER BY detected_at"),
            params)).mappings().all()

    agg: dict[tuple[str, str], dict] = {}
    entrants: list[dict] = []
    exits: list[dict] = []
    for r in rows:
        key = (r["wallet"].lower(), r["asset"])
        g = agg.setdefault(key, {"wallet": r["wallet"], "asset": r["asset"],
                                 "net": 0.0, "types": set(), "last_side": None,
                                 "last_at": None})
        nd = float(r["notional_delta"]) if r["notional_delta"] is not None else 0.0
        g["net"] += nd
        g["types"].add(r["event_type"])
        g["last_side"] = r["side"]
        g["last_at"] = r["detected_at"]
        if r["event_type"] == "OPEN":
            entrants.append({"wallet": r["wallet"], "asset": r["asset"],
                             "side": r["side"], "notional": round(abs(nd), 2),
                             "at": r["detected_at"].isoformat()})
        elif r["event_type"] == "CLOSE" and not (r["size_after"] or 0):
            exits.append({"wallet": r["wallet"], "asset": r["asset"],
                          "side": r["side"], "notional": round(abs(nd), 2),
                          "at": r["detected_at"].isoformat()})

    # D3: FLIP prominence weighted by flip_accuracy when n >= 10 — rated
    # flippers' moves rank as |net| x (0.5 + accuracy) (an accurate flipper
    # up-weights to 1.5x, a proven-bad one down-weights toward 0.5x); flips
    # without a rating and non-flip moves rank unweighted, and the card shows
    # "(unrated)" for unrated flips. Needs fill stats BEFORE ranking.
    all_wallets = sorted({g["wallet"].lower() for g in agg.values()}
                         | {e["wallet"].lower() for e in entrants[:ENTRANT_EXIT_MAX]}
                         | {e["wallet"].lower() for e in exits[:ENTRANT_EXIT_MAX]})
    fstats: dict = {}
    try:
        from app.services.hyperliquid import fill_stats as hl_fill_stats
        if all_wallets:
            fstats = await hl_fill_stats.get_stats_bulk(all_wallets)
    except Exception:
        fstats = {}

    def _mover_key(g: dict) -> float:
        base = abs(g["net"])
        if "FLIP" in g["types"]:              # same rule as _kind below
            fs = fstats.get(g["wallet"].lower()) or {}
            if fs.get("flip_accuracy") is not None and (fs.get("flip_n") or 0) >= 10:
                return base * (0.5 + float(fs["flip_accuracy"]))
        return base

    ranked = sorted(agg.values(), key=lambda g: -_mover_key(g))[:MOVER_MAX]
    mover_wallets = sorted({g["wallet"].lower() for g in ranked}
                           | {e["wallet"].lower() for e in entrants[:ENTRANT_EXIT_MAX]}
                           | {e["wallet"].lower() for e in exits[:ENTRANT_EXIT_MAX]})
    names: dict = {}
    avs: dict = {}
    pos_notional: dict[tuple[str, str], float] = {}
    if mover_wallets:
        try:
            from app.services.copy import trader_profiles
            profs = await trader_profiles.get_profiles_bulk(mover_wallets, exchange="hl")
            names = {w: (p or {}).get("display_name") for w, p in profs.items()}
        except Exception:
            names = {}
        avs = await depth_mod.account_values(mover_wallets)
        placeholders = ",".join(f":w{i}" for i in range(len(mover_wallets)))
        wparams = {f"w{i}": w for i, w in enumerate(mover_wallets)}
        async with sf() as s:
            pcy = (await s.execute(text(
                "SELECT MAX(cycle_ts) FROM analytics_positions"))).scalar()
            if pcy is not None:
                wparams["cy"] = pcy
                prow = (await s.execute(text(
                    "SELECT wallet, asset, notional, leverage FROM analytics_positions "
                    f"WHERE cycle_ts = :cy AND wallet IN ({placeholders}) "
                    "AND side != 'flat'"), wparams)).all()
                for w, a, n, lev in prow:
                    pos_notional[(w.lower(), a)] = (
                        float(n), float(lev) if lev is not None else None)

    def _kind(types: set, net: float) -> str:
        if "FLIP" in types:
            return "flipped"
        if "OPEN" in types:
            return "opened"
        if "CLOSE" in types:
            return "closed"
        return "grew" if net > 0 else "cut"

    def _person(w: str) -> dict:
        wl = w.lower()
        fs = fstats.get(wl) or {}
        return {"wallet": w, "rank": cohort.rank_of(w),
                "display_name": names.get(wl),
                "win_rate_7d": fs.get("win_rate_7d"),
                "trades_7d": fs.get("trades_7d"),
                # Tier-2 D2: win rate never travels without PF beside it
                "profit_factor": fs.get("profit_factor"),
                "max_drawdown_7d": fs.get("max_drawdown_7d"),
                "max_drawdown_pct_7d": fs.get("max_drawdown_pct_7d"),
                "flip_accuracy": fs.get("flip_accuracy"),
                "flip_n": fs.get("flip_n"),
                "is_mm": cohort.mm_flag(w) is True}

    mover_cards = []
    for g in ranked:
        wl = g["wallet"].lower()
        cur = pos_notional.get((wl, g["asset"]))
        conv = (depth_mod.conviction(cur[0], cur[1], avs.get(wl))
                if cur is not None else None)
        mover_cards.append({
            **_person(g["wallet"]),
            "asset": g["asset"], "side": g["last_side"],
            "net_delta": round(g["net"], 2),
            "kind": _kind(g["types"], g["net"]),
            "last_at": g["last_at"].isoformat() if g["last_at"] else None,
            "conviction": conv,
        })

    def _ee(e: dict) -> dict:
        return {**e, **{k: v for k, v in _person(e["wallet"]).items()
                        if k != "wallet"}}

    entrants.sort(key=lambda e: -e["notional"])
    exits.sort(key=lambda e: -e["notional"])
    out = {
        "window": window, "asset": asset.upper() if asset else None,
        "movers": mover_cards,
        "entrants": [_ee(e) for e in entrants[:ENTRANT_EXIT_MAX]],
        "exits": [_ee(e) for e in exits[:ENTRANT_EXIT_MAX]],
        "cohort": cohort.summary(),
        "computed_at": datetime.utcnow().isoformat(),
        "resolution": "mixed",
    }
    depth_mod.record_latency("movers", (time.monotonic() - _t0) * 1000)
    return out


@router.get("/context/{asset}")
async def copy_context(asset: str) -> dict:
    """Tier-2 Part B1 — READ-ONLY smart-money context for the copy modal.
    Everything from the DB written by the sweep + in-process caches; ZERO
    venue calls on this path (grep: no httpx/URL below). This endpoint feeds
    a DISPLAY block only — it gates nothing and is called by nothing in the
    execution path.

    Payload: stance (same ±10% dead zone as the pulse tiles), SMI +
    calibrating flag, top crowd per side if one exists (core cohort), venue
    funding from the stored rollup, the Perpl market mapping (registry CACHE
    read only — cold cache just hides the link), top holders for the
    Discover link (capped), freshness stamp."""
    from app.services.analytics import depth as depth_mod
    from app.services.analytics import smi as smi_mod
    _t0 = time.monotonic()
    asset = asset.upper()
    await cohort.ensure()
    cy = await _latest_cycle()
    if cy is None:
        return {"asset": asset, "available": False, "computed_at": None,
                "resolution": "20m", "note": "no sweep cycle yet"}
    def _perpl_mapping() -> dict | None:
        # registry CACHE only (no venue call added here; the registry is kept
        # warm by the market routes). Recomputed per request — a cold-cache
        # None must not freeze into the per-cycle response cache.
        try:
            from app.services import market_registry
            m = market_registry._cache["by_symbol"].get(asset)
            if m and m.get("is_active", True):
                return {"market_id": m["market_id"], "symbol": m["symbol"]}
        except Exception:
            pass
        return None

    key = ("context", asset, cy.isoformat())
    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "perpl": _perpl_mapping()}

    sf = get_session_factory()
    async with sf() as s:
        roll = (await s.execute(text(
            "SELECT wallets_long, wallets_short, notional_long, notional_short, "
            " venue_funding, flags FROM analytics_asset_rollups "
            "WHERE cycle_ts = :cy AND asset = :a AND cohort_variant = 'core'"),
            {"cy": cy, "a": asset})).mappings().first()
        srow = (await s.execute(text(
            "SELECT cycle_ts, smi FROM analytics_smi WHERE asset = :a "
            "ORDER BY cycle_ts DESC LIMIT 1"), {"a": asset})).mappings().first()
        pos_cy = (await s.execute(text(
            "SELECT MAX(cycle_ts) FROM analytics_positions"))).scalar()
        pos_rows = []
        if pos_cy is not None:
            pos_rows = (await s.execute(text(
                "SELECT wallet, side, notional, entry_px FROM analytics_positions "
                "WHERE cycle_ts = :cy AND asset = :a AND side != 'flat' "
                "LIMIT 5000"), {"cy": pos_cy, "a": asset})).mappings().all()

    if roll is None:
        out = {"asset": asset, "available": False,
               "computed_at": cy.isoformat(), "resolution": "20m",
               "note": "asset not held by the tracked cohort this cycle"}
        _cache_put(key, out)
        return out

    nl, ns = float(roll["notional_long"]), float(roll["notional_short"])
    total = nl + ns
    x = (nl - ns) / total if total > 0 else 0.0
    stance = ("net_long" if x > STANCE_DEAD_ZONE else
              "net_short" if x < -STANCE_DEAD_ZONE else "balanced")
    n_wallets = int(roll["wallets_long"]) + int(roll["wallets_short"])

    # top crowd per side, core cohort (MM- and hedger-excluded, same as pulse)
    core_rows = [{"wallet": r["wallet"], "side": r["side"],
                  "notional": float(r["notional"]),
                  "entry_px": float(r["entry_px"]) if r["entry_px"] is not None else None}
                 for r in pos_rows if cohort.mm_flag(r["wallet"]) is not True
                 and cohort.hedger_flag(r["wallet"]) is not True]
    crowds: dict[str, dict] = {}
    for c in depth_mod.crowding_clusters([r for r in core_rows if r["entry_px"]]):
        if c["side"] not in crowds:
            crowds[c["side"]] = {"side": c["side"],
                                 "wallet_count": c["wallet_count"],
                                 "entry_lo": c["entry_lo"], "entry_hi": c["entry_hi"],
                                 "notional": c["notional"]}

    # holders for the Discover link (B3): core-cohort wallets holding the
    # asset, by notional desc, capped — a 66-wallet URL would be absurd.
    HOLDERS_CAP = 30
    by_wallet: dict[str, float] = {}
    for r in core_rows:
        w = r["wallet"].lower()
        by_wallet[w] = by_wallet.get(w, 0.0) + r["notional"]
    holders = sorted(by_wallet, key=lambda w: -by_wallet[w])

    smi_val = float(srow["smi"]) if srow else None
    hist_h = await smi_mod.history_hours()
    out = {
        "asset": asset, "available": True,
        "computed_at": (pos_cy or cy).isoformat(), "resolution": "20m",
        "stance": stance, "low_sample": n_wallets < LOW_SAMPLE_MIN,
        "wallets_long": int(roll["wallets_long"]),
        "wallets_short": int(roll["wallets_short"]),
        "notional_long": round(nl, 2), "notional_short": round(ns, 2),
        "smi": smi_val,
        "smi_calibrating": hist_h < smi_mod.CALIBRATION_HOURS,
        "crowding": {"long": crowds.get("long"), "short": crowds.get("short")}
                    if crowds else None,
        "funding_hourly": (float(roll["venue_funding"])
                           if roll["venue_funding"] is not None else None),
        "holders_total": len(holders),
        "holders": holders[:HOLDERS_CAP],
    }
    depth_mod.record_latency("context", (time.monotonic() - _t0) * 1000)
    _cache_put(key, out)
    return {**out, "perpl": _perpl_mapping()}


from fastapi import Depends
from app.routers.admin import require_admin


@router.get("/admin/smi-study")
async def smi_study_admin(asset: str | None = Query(None),
                          user=Depends(require_admin)) -> dict:
    """A6 internal-only view: the RAW study tables so the owner can watch
    evidence accumulate before anything is public. Admin JWT enforced (same
    require_admin as the moderation routes). The public track-record gate
    deliberately does NOT apply here — this is unfiltered evidence."""
    from app.services.analytics import smi_study
    sf = get_session_factory()
    async with sf() as s:
        where = "WHERE asset = :a" if asset else ""
        params = {"a": asset.upper()} if asset else {}
        stats = (await s.execute(text(
            "SELECT asset, kind, bucket, horizon, n, hit_n, hit_rate, "
            f" mean_ret, median_ret, corr, computed_at FROM analytics_smi_stats {where} "
            "ORDER BY asset, kind, horizon, bucket"), params)).mappings().all()
        cov = (await s.execute(text(
            "SELECT asset, COUNT(*) n, MIN(cycle_ts) first_obs, MAX(cycle_ts) last_obs, "
            " SUM(ret_4h IS NOT NULL) n4, SUM(ret_24h IS NOT NULL) n24, "
            " SUM(ret_72h IS NOT NULL) n72, SUM(done) n_done "
            f"FROM analytics_smi_outcomes {where} GROUP BY asset ORDER BY n DESC"),
            params)).mappings().all()
        px = (await s.execute(text(
            "SELECT COUNT(*) rows_, COUNT(DISTINCT asset) assets_, "
            " MIN(cycle_ts) first_ts, MAX(cycle_ts) last_ts, "
            " SUM(source = 'backfill') backfilled "
            "FROM analytics_price_history"))).mappings().first()
    return {
        "publish_gate": {"min_n": smi_study.PUBLISH_MIN_N,
                         "min_days": smi_study.PUBLISH_MIN_DAYS,
                         "reweight_discussion_floor": {
                             "min_days": smi_study.REWEIGHT_MIN_DAYS,
                             "min_obs_per_asset": smi_study.REWEIGHT_MIN_OBS}},
        "last_run": smi_study.get_last_run(),
        "price_history": {k: (v.isoformat() if hasattr(v, "isoformat")
                              else (int(v) if v is not None else None))
                          for k, v in dict(px).items()} if px else {},
        "outcome_coverage": [{
            "asset": r["asset"], "observations": int(r["n"]),
            "first_obs": r["first_obs"].isoformat(),
            "last_obs": r["last_obs"].isoformat(),
            "filled_4h": int(r["n4"] or 0), "filled_24h": int(r["n24"] or 0),
            "filled_72h": int(r["n72"] or 0), "done": int(r["n_done"] or 0),
        } for r in cov],
        "stats": [{
            "asset": r["asset"], "kind": r["kind"], "bucket": r["bucket"],
            "horizon": r["horizon"], "n": r["n"], "hit_n": r["hit_n"],
            "hit_rate": float(r["hit_rate"]) if r["hit_rate"] is not None else None,
            "mean_ret": float(r["mean_ret"]) if r["mean_ret"] is not None else None,
            "median_ret": float(r["median_ret"]) if r["median_ret"] is not None else None,
            "corr": float(r["corr"]) if r["corr"] is not None else None,
            "computed_at": r["computed_at"].isoformat(),
        } for r in stats],
    }
