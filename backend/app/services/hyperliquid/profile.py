"""Hyperliquid trader profile depth — live positions + resting orders.

On profile open for an HL trader we fetch `clearinghouseState` and
`frontendOpenOrders` from the public HL info API, cache 300s per wallet, and
serve a normalized shape the profile modal renders:

  * positions: coin, side, size, entry, mark (positionValue/size), leverage,
    LIQUIDATION price, uPnL, margin, notional + perpl_market_id via market_map
    (unmapped coins => perpl_market_id None => UI greys them "Not on Perpl")
  * tpsl: reduce-only trigger orders grouped under their position's coin
  * adds: non-reduce-only resting limits
  * pending_entries: trigger orders on coins with NO open position
  * copyable_pct: share of position notional in market_map-mapped coins
  * rr: risk/reward when a position has both entry and SL/TP trigger prices

No fabrication: absence of trigger orders is reported as has_tpsl=False per
position ("No on-chain TP/SL" in UI) — it never implies the trader has no exits.
"""
import asyncio
import time

from sqlalchemy import select

from app.db.database import get_session_factory
from app.db.copy_models import MarketMap
from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)
_CACHE_TTL = 300.0
_cache: dict[str, tuple[float, dict]] = {}

# Batch OPEN-column support (list rows): shares _cache with the profile modal —
# one payload, two consumers. Uncached wallets are primed in the background at
# concurrency 4 and reported pending until the cache fills; a failed fetch is
# remembered _FAIL_TTL so the row renders an honest dash instead of spinning.
# Rate math: worst case one Discover page = 25 wallets x 2 info calls / 300s
# cache = 50 requests per 5 min — far below HL public API weight limits.
_PRIME_CONCURRENCY = 4
_FAIL_TTL = 120.0
_prime_sem = asyncio.Semaphore(_PRIME_CONCURRENCY)
_priming: set[str] = set()
_failed: dict[str, float] = {}

# Exact open-time resolution for positions older than the ~2000-fill window:
# the FUNDING ledger reaches back to launch (a position pays funding hourly, so
# its current streak's first funding entry pins the open to ~1h accuracy).
# Resolved per wallet in a budgeted background binary search over shared
# userFunding probes (one probe dates ALL coins at that instant); results are
# cached process-wide — an open time never changes while the streak lives.
_FUNDING_FLOOR_MS = 1_700_000_000_000   # Nov 2023 — before any HL perp we track
_DATING_BUDGET = 120                    # max userFunding probes per wallet run
# Ledger granularity is hourly for recent history but AGGREGATED TO DAILY
# (00:00 entries) for older history — a probe window must span >24h so an open
# position is never misread as closed inside a sparse region.
_PROBE_WINDOW_MS = 26 * 3600 * 1000
_REFINE_TARGET_MS = 24 * 3600 * 1000    # boundary precision = old-ledger granularity
# Hour-resolution dating (dating-defect fix, 2026-08-31): for recent history
# HL serves PER-PAYMENT funding entries (hourly) carrying the position size at
# payment (delta.szi). Scanning them backward finds the streak boundary at
# hour resolution — sign flip / zero size / a gap in the coin's own hourly
# chain — which the daily-bucket binary search below CANNOT see (an intraday
# flat-and-reopen is invisible at day granularity). The daily method remains
# ONLY for streaks older than the hourly window; its dates are labeled
# 'funding' = day-resolution. Confidence stored in the source column:
#   'fill'    EXACT — from fills
#   'fund_hr' HOUR  — hourly funding boundary (sub-hour flats invisible)
#   'funding' DAY   — daily-aggregated ledger (intraday flats invisible)
#   'bound'   open before X (nothing better provable)
_HOURLY_SCAN_DAYS = 14           # how far back per-payment entries are scanned
_HOURLY_SCAN_BUDGET = 60         # requests per wallet scan (500-entry cap =>
                                 # HF wallets need forward pagination inside
                                 # each window)
_HOURLY_WINDOW_MS = 24 * 3600 * 1000
_BOUNDARY_GAP_MS = int(2.5 * 3600 * 1000)   # >2.5h hole in a coin's chain = flat
# A gap only counts as flat evidence when OTHER entries exist inside it —
# otherwise it may be the ledger's daily-aggregation region or a quiet
# account, and claiming a boundary there would fabricate a date.
_PLAUSIBLE_TOL = 0.10            # avg entry within ±10% of the dated day's range
_PLAUSIBLE_MIN_MARKS = 10        # need this many stored marks to judge a day

# Truncated cached dates are re-verified through the hourly scan ONCE per
# process per streak (the scan is per-wallet and bounded) — without this, a
# stale exact date on a high-churn wallet survives forever because the fill
# window can no longer reach its streak start (the reported defect).
_streak_reverified: set[tuple] = set()

_dating_inflight: set[str] = set()
_dating_failed_at: dict[str, float] = {}   # wallet -> monotonic ts of last failed run
_DATING_RETRY_COOLDOWN = 180.0
_PROBE_PACE_SEC = 0.8                      # server IP shares HL budget with ws/ingest/tracker
# Foreground priority: probe traffic saturates the venue's per-IP budget and
# was observed inflating live profile opens to 20s+. Probes yield while any
# foreground profile fetch is in flight, and only ONE wallet dates at a time.
_fg_active = [0]
_dating_gate = asyncio.Semaphore(1)


async def _yield_to_foreground() -> None:
    waited = 0.0
    while _fg_active[0] > 0 and waited < 30.0:
        await asyncio.sleep(0.3)
        waited += 0.3

# Open dates persist in hl_position_open_cache (PROFILE_QUALITY_REPORT A2):
# a (wallet, coin, side_sign) streak is dated ONCE ever — opened_at when
# resolvable (source 'fill' exact / 'funding' day-accurate), else the honest
# opened_before bound. Rows exist even for failed resolution ("attempted"), so
# the UI never shows "dating…" again for a position that was already tried.
# Reopened positions on the OPPOSITE side miss the key (side_sign) and re-date;
# same-side reopens are corrected by the fills recheck inside the worker.


def _sign(side: str) -> int:
    return 1 if side == "long" else -1


async def _load_open_cache(wallet: str) -> dict[tuple[str, int], dict]:
    from sqlalchemy import text
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT coin, side_sign, opened_at, opened_before, source "
            "FROM hl_position_open_cache WHERE wallet = :w"), {"w": wallet})).fetchall()
    from datetime import timezone as _tz
    out: dict[tuple[str, int], dict] = {}
    for coin, sign, oa, ob, src in rows:
        # stored as UTC DATETIME — attach tzinfo before .timestamp() (a naive
        # .timestamp() would interpret in server-local time and shift the date)
        out[(coin, int(sign))] = {
            "opened_at": oa.replace(tzinfo=_tz.utc).timestamp() if oa else None,
            "opened_before": ob.replace(tzinfo=_tz.utc).timestamp() if ob else None,
            "source": src,
        }
    return out


async def _store_open_cache(wallet: str, coin: str, side_sign: int,
                            opened_at: float | None, opened_before: float | None,
                            source: str | None) -> None:
    from datetime import datetime as _dt
    from sqlalchemy import text
    sf = get_session_factory()
    async with sf() as s:
        await s.execute(text(
            "INSERT INTO hl_position_open_cache "
            "(wallet, coin, side_sign, opened_at, opened_before, source, resolved_at) "
            "VALUES (:w, :c, :s, :oa, :ob, :src, UTC_TIMESTAMP()) "
            "ON DUPLICATE KEY UPDATE opened_at = VALUES(opened_at), "
            "opened_before = VALUES(opened_before), source = VALUES(source), "
            "resolved_at = VALUES(resolved_at)"),
            {"w": wallet, "c": coin, "s": side_sign,
             "oa": _dt.utcfromtimestamp(opened_at) if opened_at else None,
             "ob": _dt.utcfromtimestamp(opened_before) if opened_before else None,
             "src": source})
        await s.commit()


async def _market_map() -> dict[str, int | None]:
    sf = get_session_factory()
    async with sf() as session:
        rows = (await session.execute(
            select(MarketMap).where(MarketMap.exchange == "hl")
        )).scalars().all()
    return {r.native_symbol.upper(): r.perpl_market_id for r in rows}


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def _funding_search(wallet: str, targets: dict[str, str],
                          priority: list[str]) -> dict[str, tuple[str, float]]:
    """Date each target coin's current streak from the funding ledger.
    Shared probes — one userFunding request tells, for every coin at once,
    whether it was open around that instant. Returns per coin either
    ('opened_at', unix) for converged brackets or ('opened_before', unix)
    honest bound. Refinement prioritizes `priority` order (largest notional
    first per PROFILE_QUALITY_REPORT A2.4)."""
    results: dict[str, tuple[str, float]] = {}
    if True:
        now_ms = int(time.time() * 1000)
        first_seen_ts: dict[str, float] = {}   # coin -> min funding entry time seen (ms)
        budget = [_DATING_BUDGET]

        if True:   # (kept indentation — was the per-call AsyncClient block)
            async def probe(t_ms: float) -> dict[str, float]:
                """Coins with funding entries in the probe window -> min entry
                time (ms). Backs off and retries on 429 — the server IP shares
                HL's rate budget with the ws/ingest/tracker services."""
                if budget[0] <= 0:
                    return {}
                budget[0] -= 1
                await _yield_to_foreground()
                for attempt in range(4):
                    r = await hl_client.post_info({
                        "type": "userFunding", "user": wallet,
                        "startTime": int(t_ms), "endTime": int(t_ms + _PROBE_WINDOW_MS)},
                        priority=hl_client.BACKGROUND, timeout=20.0)
                    if r.status_code == 429:
                        if attempt == 3:
                            r.raise_for_status()
                        await asyncio.sleep(15.0 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    break
                out: dict[str, float] = {}
                for e in (r.json() or []):
                    c = str((e.get("delta") or {}).get("coin", "")).upper()
                    t = e.get("time")
                    if c and t is not None and (c not in out or t < out[c]):
                        out[c] = float(t)
                await asyncio.sleep(_PROBE_PACE_SEC)
                return out

            # 1) exponential backward bracketing: for each coin find a probe
            #    time where it was NOT open (lo) and the earliest where it was (hi)
            state: dict[str, list[float]] = {c: [float(_FUNDING_FLOOR_MS), float(now_ms - _PROBE_WINDOW_MS)]
                                             for c in targets}
            unbracketed = set(targets)
            step = 2 * 24 * 3600 * 1000.0
            t = now_ms - _PROBE_WINDOW_MS - step
            while unbracketed and t > _FUNDING_FLOOR_MS and budget[0] > 0:
                seen = await probe(t)
                for c in list(unbracketed):
                    if c in seen:
                        state[c][1] = t
                        first_seen_ts[c] = min(first_seen_ts.get(c, seen[c]), seen[c])
                    else:
                        state[c][0] = t
                        unbracketed.discard(c)
                step *= 2
                t -= step

            # 2) shared binary refinement down to the ledger's granularity —
            #    the probe mid comes from the highest-PRIORITY unconverged coin
            #    (largest notional first), so if the budget runs out the coins
            #    the user actually looks at are the ones that got dated.
            order = [c for c in priority if c in state] + \
                    [c for c in state if c not in priority]
            while budget[0] > 0:
                lead = next((c for c in order
                             if state[c][1] - state[c][0] > _REFINE_TARGET_MS), None)
                if lead is None:
                    break
                lo, hi = state[lead]
                mid = (lo + hi) / 2
                seen = await probe(mid)
                for c, (clo, chi) in state.items():
                    if chi - clo <= _REFINE_TARGET_MS or not (clo < mid < chi):
                        continue
                    if c in seen:
                        state[c][1] = mid
                        first_seen_ts[c] = min(first_seen_ts.get(c, seen[c]), seen[c])
                    else:
                        state[c][0] = mid

            # 3) converged brackets -> exact date (first funding entry past the
            #    boundary). Unconverged -> honest opened_before bound (the
            #    earliest PROVEN-open moment). Never a first-seen artifact.
            for c, (lo, hi) in state.items():
                if hi - lo > 2 * _REFINE_TARGET_MS:
                    results[c] = ("opened_before", (hi + _PROBE_WINDOW_MS) / 1000.0)
                    continue
                ts_ms = None
                if budget[0] > 0:
                    fin = await probe(max(lo, hi - _REFINE_TARGET_MS))
                    if c in fin:
                        ts_ms = fin[c]
                if ts_ms is None:
                    cand = first_seen_ts.get(c)
                    if cand is not None and cand <= hi + _PROBE_WINDOW_MS:
                        ts_ms = cand
                if ts_ms:
                    results[c] = ("opened_at", ts_ms / 1000.0)
                else:
                    results[c] = ("opened_before", (hi + _PROBE_WINDOW_MS) / 1000.0)
        logger.info("hl funding search for %s: %d/%d dated exactly, %d probes used",
                    wallet[:10],
                    sum(1 for k, _ in results.values() if k == "opened_at"),
                    len(targets), _DATING_BUDGET - budget[0])
    return results


async def _hourly_funding_scan(wallet: str, coins: dict[str, int],
                               fills: list[dict]) -> dict[str, dict]:
    """Hour-resolution streak dating from per-payment funding entries.

    coins: {COIN: side_sign of the CURRENT position}. Walks userFunding
    backward from now over _HOURLY_SCAN_DAYS in 24h windows (forward-
    paginating inside a window whenever the venue's 500-entry response cap
    truncates it). Per coin, walking its own entry chain newest→oldest, the
    streak boundary is the first point where the size sign flips, the size
    is zero, or a >2.5h hole appears in the chain WITH other entries proving
    the ledger has data inside the hole (see module constants — a bare hole
    is never evidence). Returns per coin:
      {"kind": "hour",   "opened_ts": s, "exact_ts": s|None}  boundary found
      {"kind": "beyond", "oldest_seen": s}   streak predates the scan window
      absent — no entries at all in the window (caller decides; anomaly)
    """
    now_ms = int(time.time() * 1000)
    floor_ms = now_ms - _HOURLY_SCAN_DAYS * 24 * 3600 * 1000
    per_coin: dict[str, list[tuple[float, float]]] = {}
    all_times: list[float] = []
    budget = _HOURLY_SCAN_BUDGET
    scanned_floor = now_ms

    if True:   # (kept indentation — was the per-call AsyncClient block)
        win_end = now_ms
        while win_end > floor_ms and budget > 0:
            win_start = max(floor_ms, win_end - _HOURLY_WINDOW_MS)
            cursor = win_start
            while budget > 0:
                await _yield_to_foreground()
                budget -= 1
                r = await hl_client.post_info({
                    "type": "userFunding", "user": wallet,
                    "startTime": int(cursor), "endTime": int(win_end)},
                    priority=hl_client.BACKGROUND, timeout=20.0)
                if r.status_code == 429:
                    await asyncio.sleep(15.0)
                    continue
                r.raise_for_status()
                batch = r.json() or []
                for e in batch:
                    d = e.get("delta") or {}
                    c = str(d.get("coin", "")).upper()
                    t = float(e.get("time") or 0)
                    if not t:
                        continue
                    all_times.append(t)
                    if c in coins:
                        per_coin.setdefault(c, []).append((t, _f(d.get("szi"))))
                await asyncio.sleep(_PROBE_PACE_SEC)
                if len(batch) < 500:
                    break
                cursor = max(e.get("time", cursor) for e in batch) + 1
            scanned_floor = win_start
            win_end = win_start
    all_times.sort()

    import bisect
    def others_inside(lo: float, hi: float) -> bool:
        # any ledger entry strictly inside (lo, hi) with 30min margins —
        # proof the hole is a real flat, not a data-granularity artifact
        i = bisect.bisect_right(all_times, lo + 1_800_000)
        return i < len(all_times) and all_times[i] < hi - 1_800_000

    fills_by_coin: dict[str, list[float]] = {}
    for f in fills or []:
        t = f.get("time")
        if t is not None:
            fills_by_coin.setdefault(str(f.get("coin", "")).upper(), []).append(float(t))

    out: dict[str, dict] = {}
    for coin, sign in coins.items():
        entries = sorted(per_coin.get(coin, []), reverse=True)   # newest first
        if not entries:
            continue
        boundary_newer: float | None = None
        for i in range(1, len(entries)):
            newer_t, _newer_sz = entries[i - 1]
            older_t, older_sz = entries[i]
            if older_sz == 0 or (older_sz > 0) != (sign > 0):
                boundary_newer = newer_t
                break
            if newer_t - older_t > _BOUNDARY_GAP_MS and others_inside(older_t, newer_t):
                boundary_newer = newer_t
                break
        if boundary_newer is None:
            oldest_t = entries[-1][0]
            if oldest_t - scanned_floor > _BOUNDARY_GAP_MS and others_inside(scanned_floor, oldest_t):
                boundary_newer = oldest_t   # ledger has data before — coin absent
            else:
                out[coin] = {"kind": "beyond", "oldest_seen": oldest_t / 1000.0}
                continue
        opened_ts = boundary_newer / 1000.0
        # fills refinement: the first coin fill inside the boundary hour
        exact = None
        for ft in sorted(fills_by_coin.get(coin, [])):
            if boundary_newer - 3_600_000 <= ft <= boundary_newer:
                exact = ft / 1000.0
                break
        out[coin] = {"kind": "hour", "opened_ts": opened_ts, "exact_ts": exact}
    return out


async def _day_price_range(coin: str, day_ts: float) -> tuple[float, float] | None:
    """(lo, hi) of stored venue marks for the UTC day of day_ts — None when
    coverage is too thin to judge (price history exists since 2026-08-26)."""
    from datetime import datetime as _dt, timezone as _tz
    from sqlalchemy import text
    d0 = _dt.fromtimestamp(day_ts, tz=_tz.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sf = get_session_factory()
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT COUNT(*), MIN(mark), MAX(mark) FROM analytics_price_history "
            "WHERE asset = :a AND cycle_ts >= :d0 AND cycle_ts < :d0 + INTERVAL 1 DAY"),
            {"a": coin, "d0": d0.replace(tzinfo=None)})).first()
    if not row or int(row[0] or 0) < _PLAUSIBLE_MIN_MARKS:
        return None
    return float(row[1]), float(row[2])


def _position_open_times(fills: list[dict], positions: list[dict]) -> None:
    """Attach opened_at / opened_before (unix seconds) to each position dict.

    Real fill data only: scanning this coin's fills newest→oldest, the current
    position streak began at the most recent fill whose start position was flat
    (or on the opposite side — a cross-through-zero fill). HL's API keeps only
    the most recent ~2000 fills, so when the open predates the window we report
    opened_at=None + opened_before=<oldest provable moment it was already open>
    — the UI shows an honest ">Nd", never a made-up date.
    """
    if not positions:
        return
    by_coin: dict[str, list[dict]] = {}
    oldest_any: float | None = None
    for f in fills:
        t = f.get("time")
        if t is None:
            continue
        oldest_any = t if oldest_any is None else min(oldest_any, t)
        by_coin.setdefault(str(f.get("coin", "")).upper(), []).append(f)

    for p in positions:
        p["opened_at"] = None
        p["opened_before"] = None
        coin_fills = sorted(by_coin.get(p["coin"], []),
                            key=lambda f: f["time"], reverse=True)
        cur_sign = 1 if p["side"] == "long" else -1
        found = False
        for f in coin_fills:
            spos = _f(f.get("startPosition"))
            if spos == 0 or (spos > 0) != (cur_sign > 0):
                p["opened_at"] = f["time"] / 1000.0
                found = True
                break
        if not found:
            if coin_fills:
                # already open at this coin's oldest visible fill
                p["opened_before"] = coin_fills[-1]["time"] / 1000.0
            elif oldest_any is not None:
                # untouched during the whole visible fill window
                p["opened_before"] = oldest_any / 1000.0


def _apply_open_cache(positions: list[dict], cache_rows: dict[tuple[str, int], dict]) -> list[dict]:
    """Overlay persisted open dates; returns positions with NO cache row yet."""
    missing = []
    for p in positions:
        row = cache_rows.get((p["coin"], _sign(p["side"])))
        if row is None:
            p["opened_at"] = None
            p["opened_before"] = None
            missing.append(p)
        else:
            p["opened_at"] = row.get("opened_at")
            p["opened_before"] = row.get("opened_before")
            if row.get("source"):
                p["opened_at_source"] = row["source"]
            p.pop("opened_at_pending", None)
    return missing


async def _dating_worker(wallet: str) -> None:
    """Background dating (never blocks the profile response): ONE userFills
    fetch dates recent streaks exactly (+ corrects same-side reopens of cached
    rows); the funding search covers the rest, largest notional first. Every
    attempted position gets a persisted row — opened_at or the honest
    opened_before bound — so it is never re-attempted and never shows
    "dating…" again."""
    try:
      async with _dating_gate:   # one wallet dates at a time — bounds IP pressure
        cached = _cache.get(wallet)
        positions = list(cached[1].get("positions", [])) if cached else []
        if not positions:
            return
        cache_rows = await _load_open_cache(wallet)
        targets = [p for p in positions if (p["coin"], _sign(p["side"])) not in cache_rows]

        fills: list[dict] = []
        try:
            r = await hl_client.post_info({"type": "userFills", "user": wallet},
                                          priority=hl_client.BACKGROUND, timeout=20.0)
            r.raise_for_status()
            fills = r.json() or []
        except Exception as exc:
            logger.warning("hl dating fills fetch failed for %s: %s", wallet[:10], exc)

        # Build histories from the SAME fills fetch (build_history module —
        # zero extra venue calls) + generalized streak-integrity correction:
        # any exact fills-derived streak start that disagrees with the
        # persisted dating beyond its source tolerance is a dating bug —
        # log it with both values and RE-DATE the cached row (this replaces
        # the old one-way same-side-reopen recheck; corrections now go in
        # both directions and never leave the two sources disagreeing).
        histories: dict = {}
        if fills:
            from app.services.hyperliquid import build_history
            histories, dating_bugs = build_history.derive(
                wallet, fills, positions, cache_rows)
            build_history.put(wallet, histories)
            for bug in dating_bugs:
                logger.warning(
                    "dating_mismatch(%s) %s %s sign=%d: cached %s (source=%s) vs "
                    "fills evidence %s — re-dating from fills",
                    bug["kind"], wallet[:10], bug["coin"], bug["sign"],
                    bug["cached"], bug["cached_source"], bug["fills_start"])
                if bug["kind"] == "exact":
                    await _store_open_cache(wallet, bug["coin"], bug["sign"],
                                            bug["fills_start"], None, "fill")
                    cache_rows[(bug["coin"], bug["sign"])] = {
                        "opened_at": bug["fills_start"], "opened_before": None,
                        "source": "fill"}
                else:
                    # refuted exact date, true start unknown: honest bound at
                    # the oldest provably-open moment
                    await _store_open_cache(wallet, bug["coin"], bug["sign"],
                                            None, bug["fills_start"], "bound")
                    cache_rows[(bug["coin"], bug["sign"])] = {
                        "opened_at": None, "opened_before": bug["fills_start"],
                        "source": "bound"}

        funding_targets: dict[str, str] = {}
        bounds: dict[str, float] = {}
        if targets:
            tmp = [{"coin": p["coin"], "side": p["side"]} for p in targets]
            _position_open_times(fills, tmp)
            for p, t in zip(targets, tmp):
                sign = _sign(p["side"])
                if t.get("opened_at"):
                    await _store_open_cache(wallet, p["coin"], sign, t["opened_at"], None, "fill")
                    cache_rows[(p["coin"], sign)] = {"opened_at": t["opened_at"], "opened_before": None, "source": "fill"}
                else:
                    funding_targets[p["coin"]] = p["side"]
                    if t.get("opened_before"):
                        bounds[p["coin"]] = t["opened_before"]

        # ---- implausibility re-verification of CACHED dates (the HF-wallet
        # defect): a cached exact date whose streak the fill window can no
        # longer reach (truncated history) is re-examined when the position
        # avg entry sits outside the dated day price range (tolerance
        # _PLAUSIBLE_TOL against stored 20-min marks). Those coins ride the
        # same hourly funding scan as fresh targets — the scan is per-wallet,
        # so adding coins is free.
        reverify: dict[str, dict] = {}
        for p in positions:
            key = (p["coin"], _sign(p["side"]))
            row = cache_rows.get(key)
            h = histories.get(key)
            if not row or not row.get("opened_at") or row.get("source") == "bound":
                continue
            if h is not None and not h.get("truncated"):
                continue   # fills reach the streak start — the dating_bugs
                           # path above already confirms/corrects this date.
            # h None = the coin has ZERO fills in the window — an even more
            # truncated case than a partial history; the cached date is just
            # as unconfirmable and re-verifies the same way.
            implausible = False
            rng = await _day_price_range(p["coin"], row["opened_at"])
            if rng is not None:
                lo, hi = rng
                entry = p.get("entry_px") or 0
                if entry and (entry < lo * (1 - _PLAUSIBLE_TOL)
                              or entry > hi * (1 + _PLAUSIBLE_TOL)):
                    implausible = True
                    logger.warning(
                        "dating_implausible %s %s sign=%d: avg entry %s outside the "
                        "dated day range %.6g..%.6g (opened_at=%s source=%s) — "
                        "re-dating via hourly funding scan",
                        wallet[:10], p["coin"], key[1], entry, lo, hi,
                        row["opened_at"], row.get("source"))
            skey = (wallet, p["coin"], key[1], row["opened_at"])
            if implausible or skey not in _streak_reverified:
                _streak_reverified.add(skey)
                reverify[p["coin"]] = {"sign": key[1], "old": dict(row),
                                       "implausible": implausible}

        # ---- hour-resolution funding scan (fresh targets + re-verifications).
        # The daily binary search remains ONLY for streaks older than the
        # hourly window; those dates are stored as source 'funding' = DAY.
        scan_coins = {c: _sign(sd) for c, sd in funding_targets.items()}
        scan_coins.update({c: v["sign"] for c, v in reverify.items()})
        hr: dict[str, dict] = {}
        if scan_coins:
            try:
                hr = await _hourly_funding_scan(wallet, scan_coins, fills)
            except Exception as exc:
                logger.warning("hourly funding scan failed for %s: %s (daily fallback)",
                               wallet[:10], exc)

        for coin, sd in list(funding_targets.items()):
            res = hr.get(coin)
            if res and res["kind"] == "hour":
                sign = _sign(sd)
                if res.get("exact_ts"):
                    ts, src = res["exact_ts"], "fill"
                else:
                    ts, src = res["opened_ts"], "fund_hr"
                await _store_open_cache(wallet, coin, sign, ts, None, src)
                cache_rows[(coin, sign)] = {"opened_at": ts, "opened_before": None, "source": src}
                del funding_targets[coin]
            elif res and res["kind"] == "beyond":
                # provably open at the oldest scanned funding payment
                bounds[coin] = min(bounds.get(coin) or res["oldest_seen"], res["oldest_seen"])

        for coin, v in reverify.items():
            res = hr.get(coin)
            sign = v["sign"]
            old_ts = v["old"]["opened_at"]
            if res and res["kind"] == "hour":
                # the hourly ledger proves a boundary the stale date missed
                if res.get("exact_ts"):
                    ts, src = res["exact_ts"], "fill"
                else:
                    ts, src = res["opened_ts"], "fund_hr"
                if abs(ts - (old_ts or 0)) > 3600:
                    logger.warning("dating_mismatch(hour_scan) %s %s: cached %s -> %s (%s)%s",
                                   wallet[:10], coin, old_ts, ts, src,
                                   " [implausible]" if v.get("implausible") else "")
                    await _store_open_cache(wallet, coin, sign, ts, None, src)
                    cache_rows[(coin, sign)] = {"opened_at": ts, "opened_before": None, "source": src}
            elif res and res["kind"] == "beyond":
                if v.get("implausible"):
                    # streak provably older than the hourly window yet the
                    # dated day is refuted by the price range — honest bound
                    ob = res["oldest_seen"]
                    logger.warning("dating_implausible downgraded %s %s: %s -> open before %s (bound)",
                                   wallet[:10], coin, old_ts, ob)
                    await _store_open_cache(wallet, coin, sign, None, ob, "bound")
                    cache_rows[(coin, sign)] = {"opened_at": None, "opened_before": ob, "source": "bound"}
                # plausible/unjudgeable + continuously funded through the whole
                # hourly window: the cached date stands (a hidden flat OLDER
                # than the window is unknowable — documented residual)
            # absent from scan: an open position with zero funding entries in
            # the window is a ledger anomaly — leave the row, no evidence

        if funding_targets:
            priority = [p["coin"] for p in sorted(
                (p for p in targets if p["coin"] in funding_targets),
                key=lambda p: -(p.get("notional") or 0))]
            res = await _funding_search(wallet, funding_targets, priority)
            for coin, side in funding_targets.items():
                sign = _sign(side)
                kind, ts = res.get(coin) or ("opened_before", bounds.get(coin) or time.time())
                if kind == "opened_at":
                    await _store_open_cache(wallet, coin, sign, ts, None, "funding")
                    cache_rows[(coin, sign)] = {"opened_at": ts, "opened_before": None, "source": "funding"}
                else:
                    ob = min(ts, bounds.get(coin) or ts)
                    await _store_open_cache(wallet, coin, sign, None, ob, "bound")
                    cache_rows[(coin, sign)] = {"opened_at": None, "opened_before": ob, "source": "bound"}

        _apply_open_cache(positions, cache_rows)
    except Exception as exc:
        logger.warning("hl dating worker failed for %s: %s", wallet[:10], exc)
        _dating_failed_at[wallet] = time.monotonic()
        cached = _cache.get(wallet)
        if cached:
            for p in cached[1].get("positions", []):
                p.pop("opened_at_pending", None)
    finally:
        _dating_inflight.discard(wallet)


async def get_profile_state(wallet: str, deep: bool = True,
                            priority: str = hl_client.CRITICAL) -> dict:
    """deep=True (profile modal opens): the background worker also refreshes
    the per-position build history from its single userFills fetch. Discover
    priming passes deep=False — list rows need no history and priming 25
    wallets a page must not add 25 userFills calls.

    priority: weight class for the two venue calls (Wallet Explorer Part 2 —
    the explorer passes hl_client.EXPLORER so its traffic sheds first; every
    existing caller keeps the default CRITICAL)."""
    wallet = (wallet or "").lower()
    now = time.monotonic()
    c = _cache.get(wallet)
    if c and now - c[0] < _CACHE_TTL:
        return c[1]

    # Blocking path = TWO venue calls only (PROFILE_QUALITY_REPORT A2.1) —
    # open-date resolution runs in the background against the persistent cache.
    # _fg_active makes background probes yield while this fetch is in flight.
    _fg_active[0] += 1
    try:
        st_resp, oo_resp = await asyncio.gather(
            hl_client.post_info({"type": "clearinghouseState", "user": wallet},
                                priority=priority, timeout=15.0),
            hl_client.post_info({"type": "frontendOpenOrders", "user": wallet},
                                priority=priority, timeout=15.0),
        )
        st_resp.raise_for_status()
        oo_resp.raise_for_status()
        state = st_resp.json()
        orders = oo_resp.json() or []
    finally:
        _fg_active[0] -= 1

    mmap = await _market_map()

    positions = []
    total_notional = 0.0
    mapped_notional = 0.0
    for ap in state.get("assetPositions", []):
        p = ap.get("position") or {}
        szi = _f(p.get("szi"))
        if szi == 0:
            continue
        size = abs(szi)
        notional = _f(p.get("positionValue"))
        coin = str(p.get("coin", "")).upper()
        perpl_mid = mmap.get(coin)
        total_notional += notional
        if perpl_mid is not None:
            mapped_notional += notional
        positions.append({
            "coin": coin,
            "side": "long" if szi > 0 else "short",
            "size": size,
            "entry_px": _f(p.get("entryPx")),
            # HL doesn't include mark in this payload; positionValue/size IS the
            # mark-derived notional per unit (real data, not an estimate label).
            "mark_px": (notional / size) if size else 0.0,
            "leverage": _f((p.get("leverage") or {}).get("value")),
            "liquidation_px": _f(p.get("liquidationPx")) or None,
            "unrealized_pnl": _f(p.get("unrealizedPnl")),
            "margin_used": _f(p.get("marginUsed")),
            "notional": notional,
            "perpl_market_id": perpl_mid,
            # Wallet Explorer Part 2 (additive — same payload, no new calls):
            "roe": _f(p.get("returnOnEquity")) if p.get("returnOnEquity") is not None else None,
            "funding_since_open": _f((p.get("cumFunding") or {}).get("sinceOpen"))
                if (p.get("cumFunding") or {}).get("sinceOpen") is not None else None,
            "margin_mode": str((p.get("leverage") or {}).get("type") or "") or None,
        })

    # Open dates from the PERSISTENT cache (dated once per streak, ever).
    # Positions with no row yet show "dating…" only while the one background
    # attempt runs; after that a row always exists (date or honest bound).
    try:
        cache_rows = await _load_open_cache(wallet)
    except Exception:
        cache_rows = {}
    missing = _apply_open_cache(positions, cache_rows)
    failed_at = _dating_failed_at.get(wallet)
    in_cooldown = failed_at is not None and time.monotonic() - failed_at < _DATING_RETRY_COOLDOWN
    from app.services.hyperliquid import build_history as _bh
    need_history = bool(deep and positions and _bh.is_stale(wallet, _CACHE_TTL))
    spawn_dating = ((bool(missing) or need_history)
                    and wallet not in _dating_inflight and not in_cooldown)
    if missing and (spawn_dating or wallet in _dating_inflight):
        for p in missing:
            p["opened_at_pending"] = True   # only until this streak's ONE attempt

    pos_coins = {p["coin"] for p in positions}
    tpsl: dict[str, list[dict]] = {}
    adds: list[dict] = []
    pending_entries: list[dict] = []
    for o in orders:
        coin = str(o.get("coin", "")).upper()
        row = {
            "coin": coin,
            "side": "buy" if o.get("side") == "B" else "sell",
            "limit_px": _f(o.get("limitPx")),
            "trigger_px": _f(o.get("triggerPx")) or None,
            "size": _f(o.get("sz")),
            "is_trigger": bool(o.get("isTrigger")),
            "reduce_only": bool(o.get("reduceOnly")),
            "order_type": o.get("orderType"),
            "tpsl": bool(o.get("isPositionTpsl")),
        }
        is_reduce_trigger = row["is_trigger"] and (row["reduce_only"] or row["tpsl"])
        if is_reduce_trigger and coin in pos_coins:
            tpsl.setdefault(coin, []).append(row)
        elif row["is_trigger"] and coin not in pos_coins:
            pending_entries.append(row)
        elif not row["is_trigger"] and not row["reduce_only"]:
            adds.append(row)
        else:
            # reduce-only non-trigger limits on an open position: closing limits
            tpsl.setdefault(coin, []).append(row)

    # attach TP/SL + R:R per position
    for p in positions:
        rows = tpsl.get(p["coin"], [])
        p["tpsl_orders"] = rows
        p["has_tpsl"] = len(rows) > 0
        entry = p["entry_px"]
        sl = tp = None
        for r in rows:
            trig = r["trigger_px"] or r["limit_px"]
            if not trig or not entry:
                continue
            adverse = (trig < entry) if p["side"] == "long" else (trig > entry)
            if adverse:
                sl = trig if sl is None else (max(sl, trig) if p["side"] == "long" else min(sl, trig))
            else:
                tp = trig if tp is None else (min(tp, trig) if p["side"] == "long" else max(tp, trig))
        p["sl_px"], p["tp_px"] = sl, tp
        if sl is not None and tp is not None and entry:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            p["rr"] = round(reward / risk, 2) if risk > 0 else None
        else:
            p["rr"] = None

    data = {
        "wallet": wallet,
        "account_value": _f((state.get("marginSummary") or {}).get("accountValue")),
        # Wallet Explorer Part 2 (additive, same clearinghouseState payload):
        "withdrawable": _f(state.get("withdrawable")) if state.get("withdrawable") is not None else None,
        "total_margin_used": _f((state.get("marginSummary") or {}).get("totalMarginUsed")) or None,
        "total_ntl_pos": _f((state.get("marginSummary") or {}).get("totalNtlPos")) or None,
        "maintenance_margin": _f(state.get("crossMaintenanceMarginUsed")) or None,
        "positions": positions,
        "adds": adds,
        "pending_entries": pending_entries,
        "copyable_pct": round(mapped_notional / total_notional * 100, 1) if total_notional > 0 else None,
        "fetched_at": time.time(),
    }
    _cache[wallet] = (now, data)
    _failed.pop(wallet, None)
    if spawn_dating:
        # spawned AFTER caching — the worker reads positions from _cache
        _dating_inflight.add(wallet)
        asyncio.get_event_loop().create_task(_dating_worker(wallet))
    return data


# Venue-authoritative equity curve for the profile modal (PROFILE_QUALITY C1):
# HL `portfolio` returns period-keyed pnlHistory/accountValueHistory pairs —
# full history, independent of our snapshot age. One fetch caches ALL periods
# for 10 minutes. Points come back strictly time-sorted and de-duplicated.
_PORTFOLIO_TTL = 600.0
_portfolio_cache: dict[str, tuple[float, dict[str, dict[str, list[dict]]]]] = {}
PORTFOLIO_PERIODS = {"24h": "day", "7d": "week", "30d": "month", "all": "allTime"}


def _parse_history(payload: dict, key: str) -> list[dict]:
    pts = []
    seen: set[int] = set()
    for item in (payload or {}).get(key) or []:
        try:
            t = int(item[0]) // 1000
            v = float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if t in seen:
            continue
        seen.add(t)
        pts.append({"t": t, "v": round(v, 2)})
    pts.sort(key=lambda p: p["t"])
    return pts


async def _portfolio_all(wallet: str,
                         priority: str = hl_client.CRITICAL) -> dict[str, dict[str, list[dict]]]:
    """One `portfolio` fetch caches EVERY period key HL returns (day/week/
    month/allTime AND the perp-only perpDay/... variants), each with pnl +
    account_value series. 10-minute cache, shared by the modal equity curve
    and the wallet explorer."""
    wallet = (wallet or "").lower()
    now = time.monotonic()
    c = _portfolio_cache.get(wallet)
    if not (c and now - c[0] < _PORTFOLIO_TTL):
        r = await hl_client.post_info({"type": "portfolio", "user": wallet},
                                      priority=priority, timeout=15.0)
        r.raise_for_status()
        raw = dict(r.json() or [])
        parsed: dict[str, dict[str, list[dict]]] = {}
        for period, payload in raw.items():
            p = dict(payload) or {}
            parsed[period] = {
                "pnl": _parse_history(p, "pnlHistory"),
                "account_value": _parse_history(p, "accountValueHistory"),
            }
        _portfolio_cache[wallet] = (now, parsed)
        c = _portfolio_cache[wallet]
    return c[1]


async def get_portfolio_series(wallet: str, timeframe: str) -> list[dict]:
    hl_period = PORTFOLIO_PERIODS.get(timeframe, "allTime")
    parsed = await _portfolio_all(wallet)
    return (parsed.get(hl_period) or {}).get("pnl", [])


async def get_portfolio_full(wallet: str,
                             priority: str = hl_client.CRITICAL) -> dict[str, dict[str, list[dict]]]:
    """All periods, pnl + account_value (venue-authoritative, includes
    unrealized). Wallet Explorer equity curve source."""
    return await _portfolio_all(wallet, priority=priority)


def _summary_from_state(data: dict) -> dict:
    # Same shape as the Perpl active_markets entries so cards/rows render chips
    # identically; market_id is the mapped Perpl id (None = not on Perpl).
    markets = [{"market_id": p.get("perpl_market_id"), "symbol": p["coin"],
                "side": p["side"]} for p in data.get("positions", [])]
    return {"count": len(markets), "markets": markets, "pending": False}


async def _prime_one(wallet: str) -> None:
    async with _prime_sem:
        try:
            await get_profile_state(wallet, deep=False)
        except Exception as exc:
            _failed[wallet] = time.monotonic()
            logger.warning("hl-active prime failed for %s: %s", wallet[:10], exc)
        finally:
            _priming.discard(wallet)


def get_active_batch(wallets: list[str]) -> dict[str, dict]:
    """Cache-only batch of open-position summaries; kicks background primes for
    misses. Never fabricates: uncached => pending, failed => count None."""
    now = time.monotonic()
    out: dict[str, dict] = {}
    for w in wallets:
        w = (w or "").lower()
        if not w:
            continue
        c = _cache.get(w)
        if c and now - c[0] < _CACHE_TTL:
            out[w] = _summary_from_state(c[1])
            continue
        fail_ts = _failed.get(w)
        if fail_ts is not None and now - fail_ts < _FAIL_TTL:
            out[w] = {"count": None, "markets": [], "pending": False}
            continue
        out[w] = {"count": None, "markets": [], "pending": True}
        if w not in _priming:
            _priming.add(w)
            asyncio.get_event_loop().create_task(_prime_one(w))
    return out
