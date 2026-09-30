"""Full-cohort position snapshot sweep (ANALYTICS_BUILD_REPORT Phase 1.2).

Lifespan task. Every cycle (default 20 min):
  1. cohort.ensure()/refresh gives the ranked top-400 wallet list
  2. per wallet, cheapest honest source wins:
       a. HL tracker poll-baseline fresh (<60s)  -> source 'ws'   (0 requests;
          szi/entry only — leverage/liq/upnl honestly NULL; notional from the
          live mid, skipped as flat=false if no mid is streaming)
       b. hl profile 300s cache fresh            -> source 'sweep' (0 requests;
          same clearinghouseState fields a direct fetch would give)
       c. direct clearinghouseState fetch        -> source 'sweep' (1 request)
     Budget: HARD cap REQUEST_BUDGET per cycle; sequential; PAUSE_SEC pacing;
     yields to foreground profile fetches (profile._fg_active contract);
     one 429 backoff, a second 429 HALTS the cycle. Un-swept wallets roll to
     the FRONT of the next cycle (resume index).
     NOTE the one-way cache reuse deviation from spec 1.2 ("sweep populates
     it"): the profile cache payload REQUIRES frontendOpenOrders (TP/SL data);
     writing it from a clearinghouseState-only sweep would render profiles
     with fabricated-empty trigger data. Sweep READS that cache, never writes
     it. Documented in the report.
  3. persist analytics_positions rows (flat marker when no positions)
  4. diff vs previous cycle -> flow events (skipped on the boot baseline
     cycle), map new tracker ws events, apply the documented dedupe
  5. compute analytics_asset_rollups for cohort variants 'core' (MM-excluded)
     and 'all'
  6. retention/compaction (positions 14d full -> daily to 90d; flows 90d;
     rollups 365d), logged

Degradation path (cross-phase rule): when a cycle exhausts the request budget
before covering the cohort, the NEXT cycle's interval grows 1.5x (capped at
DEGRADED_MAX_INTERVAL); a fully-covered cycle resets it. Logged either way.
"""
import asyncio
import time
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.database import get_session_factory
from app.services.analytics import cohort, flow_events
from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

INTERVAL_SEC = 1200                 # 20-minute cycle
FIRST_RUN_DELAY_SEC = 180           # after boot: ingest/cohort settle first
REQUEST_BUDGET = 430                # HARD cap venue requests per cycle.
                                    # Tier-2 cross-part rule: was 450; shaved
                                    # to 430 so the analytics total stays at
                                    # the established 1,500/hr worst case
                                    # after the 60/hr spot sampler joined
                                    # (3x430 + 150 + 60 = 1500). Cohort is
                                    # capped at 400 wallets and cycles
                                    # measure ~290 requests — no coverage
                                    # loss even on a cold-cache cycle.
PAUSE_SEC = 1.2                     # between fetches (fill-stats discipline)
RATE_LIMIT_BACKOFF_SEC = 20         # one backoff; second 429 halts the cycle
TRACKER_FRESH_SEC = 60.0            # poll-baseline age accepted as live
DEGRADED_MAX_INTERVAL = 3600
RETENTION_POSITIONS_FULL_DAYS = 14
RETENTION_POSITIONS_DAILY_DAYS = 90
RETENTION_FLOWS_DAYS = 90
RETENTION_ROLLUPS_DAYS = 365

_task: asyncio.Task | None = None
_last_run: dict = {}
_prev_positions: dict[str, dict[str, dict]] = {}   # wallet -> {asset: row}
_baseline_done = False                              # first cycle emits no events
_resume_queue: list[str] = []                       # wallets rolled from last cycle
_current_interval = INTERVAL_SEC


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_state(state: dict, builder_dex: bool = False) -> tuple[float, dict[str, dict]]:
    """clearinghouseState -> (account_value, {asset: position row}). Same field
    mapping as hyperliquid/profile.py so both consumers agree. Builder-dex
    (HIP-3) coins keep the venue's full name, e.g. 'xyz:BRENTOIL' — uppercasing
    would write 'XYZ:BRENTOIL', which matches nothing in hl_fill_events."""
    account_value = _f((state.get("marginSummary") or {}).get("accountValue"))
    out: dict[str, dict] = {}
    for ap in state.get("assetPositions", []):
        p = ap.get("position") or {}
        szi = _f(p.get("szi"))
        if szi == 0:
            continue
        size = abs(szi)
        notional = _f(p.get("positionValue"))
        coin = str(p.get("coin", ""))
        if not builder_dex:
            coin = coin.upper()
        lev = p.get("leverage") or {}
        out[coin] = {
            "side": "long" if szi > 0 else "short",
            "size": size,
            "notional": notional,
            "entry_px": _f(p.get("entryPx")) or None,
            "leverage": _f(lev.get("value")) or None,
            "liq_px": _f(p.get("liquidationPx")) or None,
            "upnl": _f(p.get("unrealizedPnl")),
            "margin_mode": str(lev.get("type") or "")[:8] or None,
        }
    return account_value, out


def _from_tracker(wallet: str) -> dict[str, dict] | None:
    """Tracker poll-baseline reuse (source 'ws'). None when not covered/fresh.
    Thin fields stay NULL — never invented."""
    from app.services.hyperliquid import tracker as hl_tracker
    from app.services.hyperliquid import prices as hl_prices
    if wallet not in getattr(hl_tracker, "_poll_baseline", {}):
        return None
    # the tracker refreshes its poll tier every POLL_INTERVAL (15s); treat the
    # baseline as live only while the poll loop is actually running
    if not hl_tracker.get_stats().get("tracked"):
        return None
    baseline = hl_tracker._poll_baseline.get(wallet) or {}
    out: dict[str, dict] = {}
    for coin, pos in baseline.items():
        szi = _f(pos.get("szi"))
        if szi == 0:
            continue
        mid = hl_prices.get_mid(coin)
        if mid is None:
            return None   # can't state a notional honestly -> full fetch instead
        out[coin] = {
            "side": "long" if szi > 0 else "short",
            "size": abs(szi),
            "notional": abs(szi) * mid,
            "entry_px": _f(pos.get("entry")) or None,
            "leverage": None, "liq_px": None, "upnl": None, "margin_mode": None,
        }
    return out


def _triggers_from_profile_cache(wallet: str) -> list[dict] | None:
    """Resting TP/SL observed in the foreground profile cache (zero requests).
    None = wallet not observable this cycle (cache cold) — NOT 'no triggers'.
    Returns [] when the wallet was checked and runs no resting triggers."""
    from app.services.hyperliquid import profile as hl_profile
    c = hl_profile._cache.get(wallet)
    if not c or time.monotonic() - c[0] >= hl_profile._CACHE_TTL:
        return None
    out: list[dict] = []
    for p in c[1].get("positions", []):
        for kind, px in (("sl", p.get("sl_px")), ("tp", p.get("tp_px"))):
            if px:
                out.append({"asset": p["coin"], "side": p["side"], "kind": kind,
                            "trigger_px": float(px), "size": p.get("size")})
    return out


def _from_profile_cache(wallet: str) -> tuple[float, dict[str, dict]] | None:
    """hl profile 300s cache reuse (source 'sweep', zero requests)."""
    from app.services.hyperliquid import profile as hl_profile
    c = hl_profile._cache.get(wallet)
    if not c or time.monotonic() - c[0] >= hl_profile._CACHE_TTL:
        return None
    data = c[1]
    out: dict[str, dict] = {}
    for p in data.get("positions", []):
        out[p["coin"]] = {
            "side": p["side"], "size": p["size"], "notional": p["notional"],
            "entry_px": p["entry_px"] or None,
            "leverage": p["leverage"] or None,
            "liq_px": p["liquidation_px"],
            "upnl": p["unrealized_pnl"],
            "margin_mode": None,   # profile payload doesn't carry leverage.type
        }
    return _f(data.get("account_value")), out


async def _yield_to_foreground() -> None:
    from app.services.hyperliquid import profile as hl_profile
    fg = getattr(hl_profile, "_fg_active", None)
    waited = 0.0
    while fg and fg[0] > 0 and waited < 30.0:
        await asyncio.sleep(0.5)
        waited += 0.5


# ---- HIP-3 builder dexes (K-01) ---------------------------------------------
# clearinghouseState without `dex` returns the default dex only, so builder-dex
# positions (xyz:BRENTOIL, ...) never reached analytics_positions. A wallet's
# builder dex is queried only when its own sampled fills show that dex, which
# keeps the extra cost to ~100 requests/cycle (measured 2026-09-28: 97
# wallet-dex pairs in the swept cohort, 7-day fill window).
DEX_LIST_TTL_SEC = 86400
DEX_FILL_WINDOW_DAYS = 7
_builder_dexes: set[str] = set()
_builder_dexes_at = 0.0


async def _refresh_builder_dexes() -> bool:
    """perpDexs, cached daily. True when a venue request was spent."""
    global _builder_dexes, _builder_dexes_at
    if _builder_dexes and time.monotonic() - _builder_dexes_at < DEX_LIST_TTL_SEC:
        return False
    try:
        resp = await hl_client.post_info({"type": "perpDexs"},
                                         priority=hl_client.BACKGROUND, timeout=15.0)
        resp.raise_for_status()
        names = {str(d["name"]) for d in resp.json() if isinstance(d, dict) and d.get("name")}
        if names:
            _builder_dexes, _builder_dexes_at = names, time.monotonic()
            logger.info("position sweep: %d builder dexes: %s", len(names), sorted(names))
    except Exception as exc:
        # keep the previous list; with none yet, this cycle sweeps the default dex only
        logger.warning("position sweep: perpDexs fetch failed: %s", exc)
    return True


async def _wallet_dexes(wallets: list[str]) -> dict[str, set[str]]:
    """wallet -> builder dexes its recent sampled fills show (and perpDexs lists)."""
    if not _builder_dexes or not wallets:
        return {}
    since_ms = int((time.time() - DEX_FILL_WINDOW_DAYS * 86400) * 1000)
    out: dict[str, set[str]] = {}
    sf = get_session_factory()
    async with sf() as s:
        for i in range(0, len(wallets), 500):
            chunk = wallets[i:i + 500]
            params = {f"w{j}": w for j, w in enumerate(chunk)}
            params["since"] = since_ms
            rows = (await s.execute(text(
                "SELECT DISTINCT wallet_address, SUBSTRING_INDEX(coin, ':', 1) "
                "FROM hl_fill_events WHERE coin LIKE '%:%' AND ts_ms > :since "
                f"AND wallet_address IN ({','.join(':w%d' % j for j in range(len(chunk)))})"),
                params)).all()
            for w, d in rows:
                if d in _builder_dexes:
                    out.setdefault(w.lower(), set()).add(d)
    return out


async def _fetch_state(wallet: str, dex: str | None):
    """One clearinghouseState read. Returns (status_code, json | None)."""
    body = {"type": "clearinghouseState", "user": wallet}
    if dex:
        body["dex"] = dex
    await _yield_to_foreground()
    resp = await hl_client.post_info(body, priority=hl_client.BACKGROUND, timeout=15.0)
    if resp.status_code == 429:
        return 429, None
    resp.raise_for_status()
    return resp.status_code, resp.json()


async def run_cycle() -> dict:
    """One full sweep cycle. Returns the stats dict it logs."""
    global _baseline_done, _resume_queue, _current_interval
    t0 = time.monotonic()
    cycle_ts = datetime.utcnow().replace(microsecond=0)

    await cohort.ensure()
    ranked = cohort.wallets()
    if not ranked:
        logger.warning("position sweep: cohort empty — nothing to sweep")
        return {"cycle_ts": cycle_ts.isoformat(), "cohort": 0}

    # rolled-over wallets from a budget-halted cycle sweep FIRST
    rolled = [w for w in _resume_queue if w in set(ranked)]
    order = rolled + [w for w in ranked if w not in set(rolled)]
    _resume_queue = []

    requests_used = 0
    src_counts = {"ws": 0, "sweep_cache": 0, "sweep_fetch": 0, "failed": 0,
                  "dex_fetch": 0, "hip3_wallets": 0, "hip3_positions": 0}
    swept: dict[str, dict[str, dict]] = {}
    wallet_src: dict[str, str] = {}     # per-wallet source for the DB rows
    wallet_state: dict[str, float] = {} # account value where the source knows it
    halted = False
    rate_limited_once = False

    if await _refresh_builder_dexes():
        requests_used += 1
    try:
        wallet_dexes = await _wallet_dexes(order)
    except Exception as exc:
        logger.warning("position sweep: builder-dex lookup failed, default dex only: %s", exc)
        wallet_dexes = {}

    for i, w in enumerate(order):
        dexes = sorted(wallet_dexes.get(w, ()))
        main = _from_tracker(w)
        src, src_key, account_value = "ws", "ws", None
        if main is None:
            cached = _from_profile_cache(w)
            if cached is not None:
                account_value, main = cached
                src, src_key = "sweep", "sweep_cache"
        fetches: list[str | None] = ([None] if main is None else []) + dexes
        # A wallet is read whole or not at all: its full need is reserved up
        # front, so the hard budget can never leave it half-read.
        if fetches and requests_used + len(fetches) > REQUEST_BUDGET:
            _resume_queue = order[i:]
            halted = True
            break
        parts: list[tuple[str | None, dict]] = []
        limited = failed = False
        for d in fetches:
            requests_used += 1   # reserved BEFORE the attempt (budget is hard)
            try:
                sc, state = await _fetch_state(w, d)
            except Exception as exc:
                failed = True
                logger.debug("sweep fetch failed for %s dex=%s: %s (skipped — a failed "
                             "read is never a flat position)", w[:10], d or "default", exc)
                break
            finally:
                await asyncio.sleep(PAUSE_SEC)
            if sc == 429:
                limited = True
                break
            parts.append((d, state))
        if limited:
            if rate_limited_once:
                logger.warning("position sweep: second 429 — halting cycle, "
                               "%d wallets roll to next", len(order) - i)
                _resume_queue = order[i:]
                halted = True
                break
            rate_limited_once = True
            logger.warning("position sweep: 429 — backing off %ss", RATE_LIMIT_BACKOFF_SEC)
            await asyncio.sleep(RATE_LIMIT_BACKOFF_SEC)
            _resume_queue = order[i:]   # provisional; cleared if we resume fine
            continue                    # this wallet waits for next cycle
        if failed:
            src_counts["failed"] += 1
            continue
        positions = dict(main) if main is not None else {}
        hip3 = 0
        for d, state in parts:
            av, pos = _parse_state(state, builder_dex=d is not None)
            positions.update(pos)
            if d is None:
                account_value = av
                src, src_key = "sweep", "sweep_fetch"
            else:
                src_counts["dex_fetch"] += 1
                hip3 += len(pos)
                # each builder dex is its own clearinghouse with its own
                # collateral: the wallet's equity is the sum. Unknown stays
                # unknown (tracker path): never a partial total.
                if account_value is not None:
                    account_value += av
        if hip3:
            src_counts["hip3_wallets"] += 1
            src_counts["hip3_positions"] += hip3
        swept[w] = positions
        wallet_src[w] = src
        src_counts[src_key] += 1
        if account_value is not None:
            cohort.note_sweep_state(w, account_value,
                                    {a: r["notional"] for a, r in positions.items()})
            wallet_state[w] = account_value
    if not halted:
        _resume_queue = []

    # ---- persist position rows (flat marker when a swept wallet has none) ----
    rows = []
    for w, positions in swept.items():
        src = wallet_src.get(w, "sweep")
        if not positions:
            rows.append({"cy": cycle_ts, "w": w, "a": "__FLAT__", "sd": "flat",
                         "sz": 0.0, "no": 0.0, "ep": None, "lv": None,
                         "lq": None, "up": None, "mm": None, "src": src})
            continue
        for a, r in positions.items():
            rows.append({"cy": cycle_ts, "w": w, "a": a, "sd": r["side"],
                         "sz": r["size"], "no": r["notional"], "ep": r["entry_px"],
                         "lv": r["leverage"], "lq": r["liq_px"], "up": r["upnl"],
                         "mm": r["margin_mode"], "src": src})
    if rows:
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(
                "INSERT IGNORE INTO analytics_positions "
                "(cycle_ts, wallet, asset, side, size, notional, entry_px, "
                " leverage, liq_px, upnl, margin_mode, source) "
                "VALUES (:cy, :w, :a, :sd, :sz, :no, :ep, :lv, :lq, :up, :mm, :src)"
            ), rows)
            await s.commit()

    # ---- wallet state (Phase 3: conviction + account-value bands) ----------
    # Only sources that actually saw an account value write a row; the tracker
    # ws path doesn't know it and writes nothing (never invented).
    ws_rows = [{"cy": cycle_ts, "w": w, "av": av,
                "gn": round(sum(r["notional"] for r in swept[w].values()), 2),
                "na": len(swept[w])}
               for w, av in wallet_state.items() if av > 0]
    if ws_rows:
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(
                "INSERT IGNORE INTO analytics_wallet_state "
                "(cycle_ts, wallet, account_value, gross_notional, n_assets) "
                "VALUES (:cy, :w, :av, :gn, :na)"), ws_rows)
            await s.commit()

    # ---- trigger observations (Phase 3: from the foreground profile cache,
    # zero requests; replace-per-wallet so each wallet keeps ONE latest
    # observation; 'chk' row = wallet was checked, honest denominator) ------
    trig_obs = 0
    trig_checked = 0
    for w in swept:
        obs = _triggers_from_profile_cache(w)
        if obs is None:
            continue
        trig_checked += 1
        trig_obs += len(obs)
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(
                "DELETE FROM analytics_trigger_obs WHERE wallet = :w"), {"w": w})
            trows = [{"t": cycle_ts, "w": w, "a": "__CHK__", "sd": "na",
                      "k": "chk", "px": 0.0, "sz": None}]
            trows += [{"t": cycle_ts, "w": w, "a": o["asset"], "sd": o["side"],
                       "k": o["kind"], "px": o["trigger_px"], "sz": o["size"]}
                      for o in obs]
            await s.execute(text(
                "INSERT INTO analytics_trigger_obs "
                "(observed_at, wallet, asset, side, kind, trigger_px, size) "
                "VALUES (:t, :w, :a, :sd, :k, :px, :sz)"), trows)
            await s.commit()

    # ---- flow events ----
    events_inserted = 0
    ws_mapped = 0
    suppressed = 0
    window_start = cycle_ts - timedelta(seconds=_current_interval + 120)
    ws_mapped = await flow_events.map_ws_events(set(ranked))
    if _baseline_done:
        all_events: list[dict] = []
        for w, positions in swept.items():
            prev = _prev_positions.get(w)
            if prev is None:
                continue   # per-wallet baseline: first sighting emits nothing
            all_events.extend(flow_events.diff_wallet(w, prev, positions, cycle_ts))
        kept, suppressed = await flow_events.suppress_ws_duplicates(all_events, window_start)
        events_inserted = await flow_events.persist(kept)
    else:
        logger.info("position sweep: baseline cycle — %d wallets snapshotted, "
                    "zero events by design", len(swept))
    for w, positions in swept.items():
        _prev_positions[w] = positions
    _baseline_done = True

    # ---- rollups ----
    rollup_rows = await _compute_rollups(cycle_ts, swept)

    # ---- Smart Money Index (Phase 2.1) — DB-only, zero venue requests ----
    smi_rows = 0
    try:
        from app.services.analytics import smi as smi_mod
        smi_rows = await smi_mod.compute_cycle(cycle_ts)
    except Exception:
        logger.exception("smi compute failed (non-fatal — next cycle rescores)")

    # ---- retention/compaction ----
    retention = await _run_retention()

    # ---- degradation path ----
    if halted:
        prev_iv = _current_interval
        _current_interval = min(int(_current_interval * 1.5), DEGRADED_MAX_INTERVAL)
        if _current_interval != prev_iv:
            logger.warning("position sweep DEGRADED: budget exhausted with %d wallets "
                           "left — interval %ds -> %ds", len(_resume_queue),
                           prev_iv, _current_interval)
    elif _current_interval != INTERVAL_SEC:
        logger.info("position sweep recovered: interval %ds -> %ds",
                    _current_interval, INTERVAL_SEC)
        _current_interval = INTERVAL_SEC

    stats = {
        "cycle_ts": cycle_ts.isoformat(), "cohort": len(ranked),
        "swept": len(swept), "requests": requests_used,
        "budget": REQUEST_BUDGET, "sources": src_counts,
        "builder_dexes": len(_builder_dexes),
        "dex_wallets": len(wallet_dexes),
        "position_rows": len(rows), "events": events_inserted,
        "ws_mapped": ws_mapped, "suppressed_dupes": suppressed,
        "rollup_rows": rollup_rows, "smi_rows": smi_rows,
        "wallet_state_rows": len(ws_rows), "trigger_checked": trig_checked,
        "trigger_obs": trig_obs, "halted": halted,
        "rolled_to_next": len(_resume_queue), "retention": retention,
        "interval_sec": _current_interval,
        "duration_sec": round(time.monotonic() - t0, 1),
    }
    _last_run.clear()
    _last_run.update(stats)
    logger.info("position sweep cycle: %s", stats)
    return stats


async def _compute_rollups(cycle_ts: datetime, swept: dict[str, dict[str, dict]]) -> int:
    """Per-asset rollups for cohort variants 'core' (mm-excluded) and 'all'.
    Computed from THIS cycle's in-memory positions (identical to the rows just
    persisted). Venue context from the 60s metaAndAssetCtxs cache."""
    from app.services import hyperliquid_client

    try:
        venue = dict(await hyperliquid_client.get_all_funding_rates())
    except Exception:
        venue = {}
    # HIP-3: one dex-scoped metaAndAssetCtxs per builder dex that has cohort
    # positions this cycle (weight 20 each, ~4 dexes) so builder-dex assets get
    # a mark, price history, funding and a real SMI divergence component.
    held_dexes = sorted({a.split(":", 1)[0] for p in swept.values() for a in p if ":" in a})
    for d in held_dexes:
        try:
            venue.update(await hyperliquid_client.get_dex_contexts(d))
        except Exception:
            logger.warning("position sweep: dex contexts failed for %s", d)

    # fresh_pct_24h inputs: dated open times for cohort wallets, from the
    # persistent dating cache. Undated positions form their own honest bucket.
    sf = get_session_factory()
    wallets_with_pos = [w for w, p in swept.items() if p]
    dated: dict[tuple[str, str], float] = {}
    if wallets_with_pos:
        placeholders = ",".join(f":w{i}" for i in range(len(wallets_with_pos)))
        params = {f"w{i}": w for i, w in enumerate(wallets_with_pos)}
        async with sf() as s:
            drows = (await s.execute(text(
                "SELECT wallet, coin, side_sign, opened_at "
                "FROM hl_position_open_cache WHERE opened_at IS NOT NULL "
                f"AND wallet IN ({placeholders})"), params)).all()
        for w, coin, sign, oa in drows:
            side = "long" if int(sign) > 0 else "short"
            from datetime import timezone as _tz
            dated[(w.lower(), f"{coin}|{side}")] = oa.replace(tzinfo=_tz.utc).timestamp()

    now_ts = time.time()
    rows = []
    for variant in ("core", "all"):
        agg: dict[str, dict] = {}
        for w, positions in swept.items():
            # CORE excludes flagged MMs and (Tier-2 C1) flagged hedgers —
            # inventory hedges are not directional bets. Unknown stays in.
            if variant == "core" and (cohort.mm_flag(w) is True
                                      or cohort.hedger_flag(w) is True):
                continue
            for a, r in positions.items():
                g = agg.setdefault(a, {
                    "wl": 0, "ws_": 0, "nl": 0.0, "ns": 0.0,
                    "levl": [], "levs": [], "ul": 0.0, "us": 0.0,
                    "fresh": 0, "dated": 0, "undated": 0})
                if r["side"] == "long":
                    g["wl"] += 1
                    g["nl"] += r["notional"]
                    g["ul"] += r["upnl"] or 0.0
                    if r["leverage"]:
                        g["levl"].append(r["leverage"])
                else:
                    g["ws_"] += 1
                    g["ns"] += r["notional"]
                    g["us"] += r["upnl"] or 0.0
                    if r["leverage"]:
                        g["levs"].append(r["leverage"])
                oa = dated.get((w, f"{a}|{r['side']}"))
                if oa is None:
                    g["undated"] += 1
                else:
                    g["dated"] += 1
                    if now_ts - oa <= 86400:
                        g["fresh"] += 1
        for a, g in agg.items():
            v = venue.get(a) or {}
            venue_oi_usd = None
            if v.get("open_interest") and v.get("mark_price"):
                venue_oi_usd = round(v["open_interest"] * v["mark_price"], 2)
            import json as _json
            rows.append({
                "cy": cycle_ts, "a": a, "var": variant,
                "wl": g["wl"], "ws": g["ws_"],
                "nl": round(g["nl"], 2), "ns": round(g["ns"], 2),
                "ll": round(sum(g["levl"]) / len(g["levl"]), 2) if g["levl"] else None,
                "ls": round(sum(g["levs"]) / len(g["levs"]), 2) if g["levs"] else None,
                "ul": round(g["ul"], 2), "us": round(g["us"], 2),
                "coi": round(g["nl"] + g["ns"], 2),
                "voi": venue_oi_usd,
                "vf": v.get("funding_rate"),
                "fp": round(100.0 * g["fresh"] / g["dated"], 2) if g["dated"] else None,
                "fl": _json.dumps({"dated": g["dated"], "undated": g["undated"],
                                   "venue_mark": v.get("mark_price")}),
            })
    if rows:
        async with sf() as s:
            await s.execute(text(
                "INSERT IGNORE INTO analytics_asset_rollups "
                "(cycle_ts, asset, cohort_variant, wallets_long, wallets_short, "
                " notional_long, notional_short, avg_lev_long, avg_lev_short, "
                " upnl_long, upnl_short, cohort_oi, venue_oi, venue_funding, "
                " fresh_pct_24h, flags) "
                "VALUES (:cy, :a, :var, :wl, :ws, :nl, :ns, :ll, :ls, :ul, :us, "
                " :coi, :voi, :vf, :fp, :fl)"), rows)
            await s.commit()

    # ---- price history (Tier-2 Part A1): one venue mark per asset per cycle
    # from the SAME 60s venue cache used above — zero extra requests. ALL
    # venue assets with a mark are recorded (not just cohort-held ones), so a
    # forward return stays computable after an asset leaves cohort books.
    px_rows = [{"cy": cycle_ts, "a": a, "m": float(v["mark_price"]),
                "src": "venue"}
               for a, v in venue.items() if v.get("mark_price")]
    if px_rows:
        async with sf() as s:
            await s.execute(text(
                "INSERT IGNORE INTO analytics_price_history "
                "(cycle_ts, asset, mark, source) VALUES (:cy, :a, :m, :src)"),
                px_rows)
            await s.commit()
    return len(rows)


RETENTION_LTE_DAYS = 30
LTE_SLICE_IDS = 5000
LTE_MAX_SLICES_PER_RUN = 600        # bounds one run to ~3M ids; the rest next cycle


async def _run_lte_retention() -> dict:
    """leader_trade_events 30-day retention (pending item 4).
    Deletes only ids already archived by backend/deploy/lte_archive.sh
    (MAX(lte_archive_log.to_id)) — no archive, no delete. Keyed PK slices,
    commit per slice (the P-05 pattern). Rows referenced by copy_orders,
    copy_live_positions or copy_paper_positions are never deleted."""
    # Runs every cycle but only acts when an archived range is still undeleted
    # (all checks below are indexed point reads). A once-per-UTC-day gate ran
    # at 00:12, before the 02:40 archive, and left each night's archive for the
    # next day (2026-09-29).
    t0 = time.monotonic()
    out = {"lte_deleted": 0, "lte_slices": 0}
    sf = get_session_factory()
    async with sf() as s:
        archived = (await s.execute(text(
            "SELECT MAX(to_id) FROM lte_archive_log"))).scalar()
        cut = (await s.execute(text(
            "SELECT id FROM leader_trade_events "
            "WHERE detected_at >= UTC_TIMESTAMP() - INTERVAL :d DAY "
            "ORDER BY detected_at LIMIT 1"), {"d": RETENTION_LTE_DAYS})).scalar()
        keep = set((await s.execute(text(
            "SELECT leader_event_id FROM copy_orders WHERE leader_event_id IS NOT NULL "
            "UNION SELECT leader_event_id FROM copy_live_positions WHERE leader_event_id IS NOT NULL "
            "UNION SELECT suggested_event_id FROM copy_live_positions WHERE suggested_event_id IS NOT NULL "
            "UNION SELECT leader_event_id FROM copy_paper_positions WHERE leader_event_id IS NOT NULL"
        ))).scalars().all())
        # lowest DELETABLE id: protected rows stay forever and must not pin the
        # scan start below the archived mark every cycle
        if keep:
            kp = {f"k{j}": v for j, v in enumerate(keep)}
            lo = (await s.execute(text(
                "SELECT MIN(id) FROM leader_trade_events WHERE id NOT IN ("
                + ",".join(f":{k}" for k in kp) + ")"), kp)).scalar()
        else:
            lo = (await s.execute(text("SELECT MIN(id) FROM leader_trade_events"))).scalar()
    if archived is None or cut is None or lo is None:
        return {"lte_skipped": "no archive yet" if archived is None else "nothing to bound"}
    hi = min(int(archived), int(cut) - 1)    # inclusive upper id
    if int(lo) > hi:
        return {}                            # every archived id already deleted
    a = int(lo)
    while a <= hi and out["lte_slices"] < LTE_MAX_SLICES_PER_RUN:
        b = min(a + LTE_SLICE_IDS - 1, hi)
        protect = [i for i in keep if a <= i <= b]
        sql = ("DELETE FROM leader_trade_events WHERE id BETWEEN :a AND :b "
               "AND detected_at < UTC_TIMESTAMP() - INTERVAL :d DAY")
        params = {"a": a, "b": b, "d": RETENTION_LTE_DAYS}
        if protect:
            sql += " AND id NOT IN (" + ",".join(f":k{j}" for j in range(len(protect))) + ")"
            params.update({f"k{j}": v for j, v in enumerate(protect)})
        async with sf() as s:
            r = await s.execute(text(sql), params)
            await s.commit()
        out["lte_deleted"] += r.rowcount
        out["lte_slices"] += 1
        a = b + 1
        await asyncio.sleep(0.05)   # let other writers in between slices
    out["lte_protected"] = len(keep)
    out["lte_done"] = a > hi
    out["lte_ms"] = int((time.monotonic() - t0) * 1000)
    return out


async def _run_retention() -> dict:
    """Spec 1.4 retention, inside the sweep task, logged via cycle stats:
    positions FULL 14d; between 14d and 90d only each wallet's LAST cycle per
    UTC day survives; beyond 90d deleted. Flows 90d. Rollups 365d."""
    sf = get_session_factory()
    out = {}
    async with sf() as s:
        r = await s.execute(text(
            "DELETE FROM analytics_positions WHERE cycle_ts < UTC_TIMESTAMP() - INTERVAL :d DAY"),
            {"d": RETENTION_POSITIONS_DAILY_DAYS})
        out["pos_expired"] = r.rowcount
        await s.commit()
    # daily downsample of the 14d..90d band: keep the last cycle per wallet
    # per UTC day. One keyed range DELETE per UTC day so each statement is a
    # PK range scan on that day alone — the old single all-band statement
    # joined on non-sargable DATE(p.cycle_ts), full-scanned the table and ran
    # 73-132s every cycle while deleting nothing (slow log, 2026-09-28).
    # Commit per day so row locks release between slices.
    t0 = time.monotonic()
    downsampled = 0
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_POSITIONS_FULL_DAYS)
    day = (datetime.utcnow() - timedelta(days=RETENTION_POSITIONS_DAILY_DAYS)
           ).replace(hour=0, minute=0, second=0, microsecond=0)
    async with sf() as s:
        while day < cutoff:
            d1 = min(day + timedelta(days=1), cutoff)
            r = await s.execute(text(
                "DELETE p FROM analytics_positions p JOIN ("
                "  SELECT wallet, MAX(cycle_ts) keep_ts FROM analytics_positions "
                "  WHERE cycle_ts >= :d0 AND cycle_ts < :d1 GROUP BY wallet"
                ") k ON k.wallet = p.wallet "
                "WHERE p.cycle_ts >= :d0 AND p.cycle_ts < :d1 "
                "  AND p.cycle_ts < k.keep_ts"),
                {"d0": day, "d1": d1})
            downsampled += r.rowcount
            await s.commit()
            day = d1
    out["pos_downsampled"] = downsampled
    out["downsample_ms"] = int((time.monotonic() - t0) * 1000)
    async with sf() as s:
        r = await s.execute(text(
            "DELETE FROM analytics_flow_events WHERE detected_at < UTC_TIMESTAMP() - INTERVAL :d DAY LIMIT 50000"),
            {"d": RETENTION_FLOWS_DAYS})
        out["flows_expired"] = r.rowcount
        r = await s.execute(text(
            "DELETE FROM analytics_asset_rollups WHERE cycle_ts < UTC_TIMESTAMP() - INTERVAL :d DAY LIMIT 50000"),
            {"d": RETENTION_ROLLUPS_DAYS})
        out["rollups_expired"] = r.rowcount
        r = await s.execute(text(
            "DELETE FROM analytics_smi WHERE cycle_ts < UTC_TIMESTAMP() - INTERVAL :d DAY LIMIT 50000"),
            {"d": RETENTION_ROLLUPS_DAYS})
        out["smi_expired"] = r.rowcount
        r = await s.execute(text(
            "DELETE FROM analytics_wallet_state WHERE cycle_ts < UTC_TIMESTAMP() - INTERVAL :d DAY LIMIT 50000"),
            {"d": RETENTION_POSITIONS_DAILY_DAYS})
        out["wallet_state_expired"] = r.rowcount
        r = await s.execute(text(
            "DELETE FROM analytics_trigger_obs WHERE observed_at < UTC_TIMESTAMP() - INTERVAL 14 DAY LIMIT 50000"))
        out["trigger_obs_expired"] = r.rowcount
        await s.commit()
    try:
        out.update(await _run_lte_retention())
    except Exception as exc:
        logger.warning("leader_trade_events retention failed: %s", exc)
    return out


async def _loop() -> None:
    await asyncio.sleep(FIRST_RUN_DELAY_SEC)
    while True:
        try:
            await cohort.refresh()
        except Exception:
            logger.exception("cohort refresh failed — sweeping with previous cohort")
        try:
            await run_cycle()
        except Exception:
            logger.exception("position sweep cycle failed — next attempt on schedule")
        await asyncio.sleep(_current_interval)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())
        logger.info("analytics position sweep started (interval %ss, budget %s req/cycle)",
                    INTERVAL_SEC, REQUEST_BUDGET)


async def stop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None


def get_last_run() -> dict:
    return dict(_last_run)
