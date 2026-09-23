"""SMI validation pipeline (ANALYTICS_TIER2_REPORT Part A).

Answers "when the SMI said X, what happened next?" — and makes it impossible
to publish a statistically meaningless result. Three stores (migration v11):

  analytics_price_history   one venue mark per asset per 20-min sweep cycle,
                            written by the sweep from the SAME 60s
                            metaAndAssetCtxs cache the rollups already use
                            (zero new venue calls). Backfilled once per boot
                            from analytics_asset_rollups.flags.venue_mark —
                            the only price series our tables already carried
                            (leaderboard snapshots hold no marks; checked).
  analytics_smi_outcomes    per SMI observation: forward price change at
                            4h / 24h / 72h. A horizon is filled ONLY once it
                            has elapsed — never estimated. `done` marks rows
                            whose longest horizon has been processed.
  analytics_smi_stats       the nightly study: per asset, per SMI bucket and
                            per 24h SMI-CHANGE bucket, per horizon: n, hit
                            rate (sign agreement), mean and median return;
                            plus per-component Spearman rank correlation.
                            Replaced wholesale each run (latest snapshot).

FORWARD-RETURN RULES (A2, all enforced here):
  * base price  = the mark stored at the observation's own cycle_ts (exact
    PK match). No base mark -> the row closes with NULL returns (honest gap).
  * future price = the FIRST price row with cycle_ts >= obs + horizon, but
    only within HORIZON_SLACK_SEC of the horizon — a "4h return" computed
    from a mark 9h later (server outage) is a lie; such gaps store NULL.
  * a horizon that has not elapsed yet stays unfilled and is retried next run.

STUDY DEFINITIONS (A3):
  * SMI buckets: 0-30 / 30-45 / 45-55 / 55-70 / 70-100 (spec).
  * 24h-change buckets (our documented choice — spec left them open):
    drop_big <= -10, drop (-10,-3], flat (-3,3), rise [3,10), rise_big >= 10.
    The 24h-ago SMI is this asset's outcomes row nearest 24h back (within
    CHANGE_SLACK_SEC); observations without one carry no change bucket.
  * hit = sign agreement: sign(smi - 50) (or sign(change)) matches
    sign(ret). Rows where either sign is 0 are excluded from the hit
    numerator/denominator (hit_n) but stay in n / mean / median. The 45-55
    bucket's hit rate is therefore near-meaningless by construction — the
    admin view shows it, the public gate does not change that.
  * component correlation: Spearman (rank) correlation of each sub-score
    c1..c5 with each horizon's return, per asset, n recorded.

PUBLICATION RULE (A4 — HARD, enforced server-side in this module, not the UI):
  a bucket/horizon row is publishable ONLY when its n >= PUBLISH_MIN_N (30)
  AND the asset's observation span >= PUBLISH_MIN_DAYS (21). No exceptions,
  no owner override in v1. track_record() is the single source the API uses;
  unpublishable rows are NOT in its payload at all.

REWEIGHTING IS OUT OF SCOPE (A5): the SMI component weights in smi.py stay
hand-set. Evidence-based reweighting is not even DISCUSSED until the study
holds >= 90 days AND >= 300 observations per asset (REWEIGHT_MIN_DAYS /
REWEIGHT_MIN_OBS below exist only to document that threshold — no code path
reads them).
"""
import asyncio
import json
import statistics
import time
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

INTERVAL_SEC = 24 * 3600            # nightly
FIRST_RUN_DELAY_SEC = 900           # after boot: sweep + price history settle
HORIZONS = {"4h": 4 * 3600, "24h": 24 * 3600, "72h": 72 * 3600}
HORIZON_SLACK_SEC = 2 * 3600        # future mark must land within horizon+2h
CHANGE_SLACK_SEC = 2 * 3600         # 24h-ago SMI lookup tolerance
GAP_CLOSE_AFTER_SEC = 7 * 86400     # unfillable rows close as NULL after 7d
OUTCOME_SCAN_DAYS = 10              # pending-outcome scan window
SMI_BUCKETS = [("0_30", 0.0, 30.0), ("30_45", 30.0, 45.0),
               ("45_55", 45.0, 55.0), ("55_70", 55.0, 70.0),
               ("70_100", 70.0, 100.0001)]
CHANGE_BUCKETS = [("drop_big", -1e9, -10.0), ("drop", -10.0, -3.0),
                  ("flat", -3.0, 3.0), ("rise", 3.0, 10.0),
                  ("rise_big", 10.0, 1e9)]
MIN_CORR_N = 10                     # below this a correlation is not stored

PUBLISH_MIN_N = 30                  # A4 — hard, server-side, no override
PUBLISH_MIN_DAYS = 21               # A4 — hard, server-side, no override

REWEIGHT_MIN_DAYS = 90              # A5 — documentation only, no code path
REWEIGHT_MIN_OBS = 300              # A5 — documentation only, no code path

RETENTION_PRICE_DAYS = 90
RETENTION_OUTCOMES_DAYS = 365

_task: asyncio.Task | None = None
_last_run: dict = {}
_backfilled = False


async def backfill_price_history() -> int:
    """One-shot per boot: recover marks from rollup flags.venue_mark (core
    variant) for cycles the price table does not have. INSERT IGNORE makes it
    idempotent; the scan is bounded to the price retention window."""
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT r.cycle_ts, r.asset, r.flags FROM analytics_asset_rollups r "
            "LEFT JOIN analytics_price_history p "
            " ON p.cycle_ts = r.cycle_ts AND p.asset = r.asset "
            "WHERE r.cohort_variant = 'core' AND p.asset IS NULL "
            " AND r.cycle_ts >= UTC_TIMESTAMP() - INTERVAL :d DAY"),
            {"d": RETENTION_PRICE_DAYS})).all()
        ins = []
        for cy, a, fl in rows:
            try:
                m = json.loads(fl).get("venue_mark") if fl else None
            except Exception:
                m = None
            if m:
                ins.append({"cy": cy, "a": a, "m": float(m), "src": "backfill"})
        if ins:
            await s.execute(text(
                "INSERT IGNORE INTO analytics_price_history "
                "(cycle_ts, asset, mark, source) VALUES (:cy, :a, :m, :src)"), ins)
            await s.commit()
    if ins:
        logger.info("smi_study: backfilled %d price rows from rollup flags", len(ins))
    return len(ins)


async def compute_outcomes() -> dict:
    """A2: fill forward returns for SMI observations whose horizons elapsed.
    DB only — zero venue calls."""
    now = datetime.utcnow()
    sf = get_session_factory()
    async with sf() as s:
        pend = (await s.execute(text(
            "SELECT m.cycle_ts, m.asset, m.smi, m.c1, m.c2, m.c3, m.c4, m.c5, "
            " o.ret_4h, o.ret_24h, o.ret_72h, o.done "
            "FROM analytics_smi m LEFT JOIN analytics_smi_outcomes o "
            " ON o.cycle_ts = m.cycle_ts AND o.asset = m.asset "
            "WHERE m.cycle_ts >= :cut AND (o.asset IS NULL OR o.done = 0) "
            "ORDER BY m.asset, m.cycle_ts"),
            {"cut": now - timedelta(days=OUTCOME_SCAN_DAYS)})).mappings().all()
        if not pend:
            return {"pending": 0, "written": 0}
        assets = sorted({r["asset"] for r in pend})
        px: dict[str, list[tuple[datetime, float]]] = {}
        placeholders = ",".join(f":a{i}" for i in range(len(assets)))
        params = {f"a{i}": a for i, a in enumerate(assets)}
        params["cut"] = (now - timedelta(days=OUTCOME_SCAN_DAYS
                                         + HORIZONS["72h"] // 86400 + 1))
        prow = (await s.execute(text(
            "SELECT asset, cycle_ts, mark FROM analytics_price_history "
            f"WHERE asset IN ({placeholders}) AND cycle_ts >= :cut "
            "ORDER BY asset, cycle_ts"), params)).all()
        for a, cy, m in prow:
            px.setdefault(a, []).append((cy, float(m)))
    px_times = {a: [t for t, _ in series] for a, series in px.items()}

    import bisect
    def price_at(asset: str, ts: datetime) -> float | None:
        times = px_times.get(asset) or []
        i = bisect.bisect_left(times, ts)
        if i < len(times) and times[i] == ts:
            return px[asset][i][1]
        return None

    def price_after(asset: str, ts: datetime, slack: int) -> float | None:
        times = px_times.get(asset) or []
        i = bisect.bisect_left(times, ts)
        if i < len(times) and (times[i] - ts).total_seconds() <= slack:
            return px[asset][i][1]
        return None

    written = 0
    ups = []
    for r in pend:
        obs_ts = r["cycle_ts"]
        base = price_at(r["asset"], obs_ts)
        rets = {"4h": r["ret_4h"], "24h": r["ret_24h"], "72h": r["ret_72h"]}
        changed = False
        all_final = True
        for h, secs in HORIZONS.items():
            if rets[h] is not None:
                continue
            target = obs_ts + timedelta(seconds=secs)
            if now < target:
                all_final = False
                continue          # horizon not elapsed — never estimate
            fut = (price_after(r["asset"], target, HORIZON_SLACK_SEC)
                   if base else None)
            if fut is not None and base:
                rets[h] = round((fut - base) / base, 6)
                changed = True
            elif (now - target).total_seconds() > GAP_CLOSE_AFTER_SEC:
                changed = True    # closes as NULL — honest gap, stop retrying
            else:
                all_final = False # price row may still arrive (rare) — retry
        if changed or (all_final and not r["done"]):
            ups.append({"cy": obs_ts, "a": r["asset"], "smi": float(r["smi"]),
                        "c1": float(r["c1"]), "c2": float(r["c2"]),
                        "c3": float(r["c3"]), "c4": float(r["c4"]),
                        "c5": float(r["c5"]),
                        "r4": rets["4h"], "r24": rets["24h"], "r72": rets["72h"],
                        "dn": 1 if all_final else 0})
            written += 1

    if ups:
        async with sf() as s:
            await s.execute(text(
                "INSERT INTO analytics_smi_outcomes "
                "(cycle_ts, asset, smi, c1, c2, c3, c4, c5, "
                " ret_4h, ret_24h, ret_72h, done) "
                "VALUES (:cy, :a, :smi, :c1, :c2, :c3, :c4, :c5, "
                " :r4, :r24, :r72, :dn) "
                "ON DUPLICATE KEY UPDATE ret_4h=VALUES(ret_4h), "
                " ret_24h=VALUES(ret_24h), ret_72h=VALUES(ret_72h), "
                " done=VALUES(done)"), ups)
            await s.commit()
    return {"pending": len(pend), "written": written}


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, ties get average ranks. None when degenerate."""
    n = len(xs)
    if n < MIN_CORR_N:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 4)


async def compute_stats() -> dict:
    """A3: the nightly study, replaced wholesale. Per-asset loops keep memory
    bounded (one asset's outcome rows at a time)."""
    sf = get_session_factory()
    async with sf() as s:
        assets = (await s.execute(text(
            "SELECT DISTINCT asset FROM analytics_smi_outcomes"))).scalars().all()
    now = datetime.utcnow()
    rows_out: list[dict] = []
    for asset in assets:
        async with sf() as s:
            obs = (await s.execute(text(
                "SELECT cycle_ts, smi, c1, c2, c3, c4, c5, "
                " ret_4h, ret_24h, ret_72h FROM analytics_smi_outcomes "
                "WHERE asset = :a ORDER BY cycle_ts"), {"a": asset}
            )).mappings().all()
        if not obs:
            continue
        # 24h SMI change per observation from the series itself
        times = [r["cycle_ts"] for r in obs]
        import bisect
        changes: list[float | None] = []
        for i, r in enumerate(obs):
            back = r["cycle_ts"] - timedelta(hours=24)
            j = bisect.bisect_left(times, back)
            prev = None
            for k in (j - 1, j):
                if 0 <= k < len(obs) and abs(
                        (times[k] - back).total_seconds()) <= CHANGE_SLACK_SEC:
                    prev = obs[k]
                    break
            changes.append(float(r["smi"]) - float(prev["smi"]) if prev else None)

        def emit(kind: str, bucket: str, horizon: str, sel: list[tuple[float, float]]):
            """sel: (signal, ret) pairs with ret known."""
            if not sel:
                return
            rets = [ret for _, ret in sel]
            signed = [(sig, ret) for sig, ret in sel if sig != 0 and ret != 0]
            hits = sum(1 for sig, ret in signed if (sig > 0) == (ret > 0))
            rows_out.append({
                "a": asset, "k": kind, "b": bucket, "h": horizon,
                "n": len(sel),
                "hn": len(signed) or None,
                "hr": round(hits / len(signed), 4) if signed else None,
                "mr": round(statistics.mean(rets), 6),
                "md": round(statistics.median(rets), 6),
                "co": None, "ca": now,
            })

        for h in HORIZONS:
            col = f"ret_{h}"
            known = [(r, changes[i]) for i, r in enumerate(obs)
                     if r[col] is not None]
            for label, lo, hi in SMI_BUCKETS:
                sel = [(float(r["smi"]) - 50.0, float(r[col]))
                       for r, _ in known if lo <= float(r["smi"]) < hi]
                emit("smi", label, h, sel)
            for label, lo, hi in CHANGE_BUCKETS:
                sel = [(ch, float(r[col]))
                       for r, ch in known if ch is not None and lo <= ch < hi]
                emit("smi_change_24h", label, h, sel)
            # component rank correlations
            for comp in ("c1", "c2", "c3", "c4", "c5"):
                xs = [float(r[comp]) for r, _ in known]
                ys = [float(r[col]) for r, _ in known]
                corr = _spearman(xs, ys)
                if corr is not None:
                    rows_out.append({
                        "a": asset, "k": "component_corr", "b": comp, "h": h,
                        "n": len(xs), "hn": None, "hr": None, "mr": None,
                        "md": None, "co": corr, "ca": now})

    async with sf() as s:
        await s.execute(text("DELETE FROM analytics_smi_stats"))
        if rows_out:
            await s.execute(text(
                "INSERT INTO analytics_smi_stats "
                "(asset, kind, bucket, horizon, n, hit_n, hit_rate, "
                " mean_ret, median_ret, corr, computed_at) "
                "VALUES (:a, :k, :b, :h, :n, :hn, :hr, :mr, :md, :co, :ca)"),
                rows_out)
        await s.commit()
    return {"assets": len(assets), "stat_rows": len(rows_out)}


async def observation_span(asset: str) -> tuple[int, float]:
    """(observations with >= one filled return, span in days) for one asset."""
    sf = get_session_factory()
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT COUNT(*), MIN(cycle_ts), MAX(cycle_ts) "
            "FROM analytics_smi_outcomes WHERE asset = :a "
            "AND (ret_4h IS NOT NULL OR ret_24h IS NOT NULL "
            "     OR ret_72h IS NOT NULL)"), {"a": asset})).first()
    n = int(row[0] or 0)
    span = ((row[2] - row[1]).total_seconds() / 86400.0) if n and row[1] else 0.0
    return n, span


async def track_record(asset: str) -> dict:
    """A4: the ONLY payload the public API may serve. Publishable rows
    (n >= PUBLISH_MIN_N) are included only when the asset's span >=
    PUBLISH_MIN_DAYS; otherwise the honest collecting state — the numbers
    themselves are withheld here, server-side, not hidden by the UI."""
    n_obs, span_days = await observation_span(asset)
    collecting = {"published": False, "collecting": True,
                  "n_observations": n_obs, "days": round(span_days, 1),
                  "publish_min_n": PUBLISH_MIN_N,
                  "publish_min_days": PUBLISH_MIN_DAYS}
    if span_days < PUBLISH_MIN_DAYS:
        return collecting
    sf = get_session_factory()
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT kind, bucket, horizon, n, hit_n, hit_rate, mean_ret, "
            " median_ret, computed_at FROM analytics_smi_stats "
            "WHERE asset = :a AND kind IN ('smi', 'smi_change_24h') "
            "AND n >= :minn"), {"a": asset, "minn": PUBLISH_MIN_N}
        )).mappings().all()
    if not rows:
        return collecting
    return {
        "published": True, "collecting": False,
        "n_observations": n_obs, "days": round(span_days, 1),
        "publish_min_n": PUBLISH_MIN_N, "publish_min_days": PUBLISH_MIN_DAYS,
        "computed_at": rows[0]["computed_at"].isoformat(),
        "rows": [{
            "kind": r["kind"], "bucket": r["bucket"], "horizon": r["horizon"],
            "n": r["n"], "hit_n": r["hit_n"],
            "hit_rate": float(r["hit_rate"]) if r["hit_rate"] is not None else None,
            "mean_ret": float(r["mean_ret"]) if r["mean_ret"] is not None else None,
            "median_ret": float(r["median_ret"]) if r["median_ret"] is not None else None,
        } for r in rows],
    }


FLIP_MOVE_PCT = 0.005          # D1: >= 0.5% move in the flipped direction
FLIP_WINDOW_SEC = 24 * 3600    # ... within 24h
FLIP_BASE_SLACK_SEC = 2400     # base mark within 2 cycles of the flip


async def compute_flip_accuracy() -> dict:
    """Tier-2 D1: per wallet, share of FLIP events followed by a >= 0.5% move
    in the flipped direction within 24h — measured against the Part-A price
    history (NULL until history covers the flip; a flip is only evaluated
    once its full 24h window has elapsed AND a base mark exists within
    FLIP_BASE_SLACK_SEC). Writes flip_accuracy/flip_n onto EXISTING
    trader_fill_stats rows only — a wallet the sampler never covered gets its
    accuracy when it first gets stats (documented)."""
    now = datetime.utcnow()
    sf = get_session_factory()
    async with sf() as s:
        flips = (await s.execute(text(
            "SELECT wallet, asset, side, detected_at FROM analytics_flow_events "
            "WHERE event_type = 'FLIP' AND detected_at <= :cut "
            "ORDER BY asset, detected_at"),
            {"cut": now - timedelta(seconds=FLIP_WINDOW_SEC)})).all()
        if not flips:
            return {"flips": 0, "wallets_updated": 0}
        assets = sorted({a for _, a, _, _ in flips})
        placeholders = ",".join(f":a{i}" for i in range(len(assets)))
        params = {f"a{i}": a for i, a in enumerate(assets)}
        prow = (await s.execute(text(
            "SELECT asset, cycle_ts, mark FROM analytics_price_history "
            f"WHERE asset IN ({placeholders}) ORDER BY asset, cycle_ts"),
            params)).all()
    px: dict[str, list] = {}
    for a, cy, m in prow:
        px.setdefault(a, []).append((cy, float(m)))
    px_times = {a: [t for t, _ in v] for a, v in px.items()}

    import bisect
    per_wallet: dict[str, list[bool]] = {}
    for w, a, side, ts in flips:
        times = px_times.get(a) or []
        i = bisect.bisect_left(times, ts)
        if i >= len(times) or (times[i] - ts).total_seconds() > FLIP_BASE_SLACK_SEC:
            continue        # no honest base mark — flip not evaluated
        base = px[a][i][1]
        end = ts + timedelta(seconds=FLIP_WINDOW_SEC)
        j = bisect.bisect_right(times, end)
        window = [m for _, m in px[a][i:j]]
        if len(window) < 2:
            continue
        if side == "long":
            hit = any(m >= base * (1 + FLIP_MOVE_PCT) for m in window)
        else:
            hit = any(m <= base * (1 - FLIP_MOVE_PCT) for m in window)
        per_wallet.setdefault(w.lower(), []).append(hit)

    updated = 0
    if per_wallet:
        async with sf() as s:
            for w, hits in per_wallet.items():
                r = await s.execute(text(
                    "UPDATE trader_fill_stats SET flip_accuracy = :fa, flip_n = :fn "
                    "WHERE wallet_address = :w"),
                    {"fa": round(sum(hits) / len(hits), 4), "fn": len(hits), "w": w})
                updated += r.rowcount
            await s.commit()
    return {"flips": len(flips), "flip_wallets": len(per_wallet),
            "wallets_updated": updated}


async def _retention() -> dict:
    sf = get_session_factory()
    out = {}
    async with sf() as s:
        r = await s.execute(text(
            "DELETE FROM analytics_price_history "
            "WHERE cycle_ts < UTC_TIMESTAMP() - INTERVAL :d DAY LIMIT 50000"),
            {"d": RETENTION_PRICE_DAYS})
        out["price_expired"] = r.rowcount
        r = await s.execute(text(
            "DELETE FROM analytics_smi_outcomes "
            "WHERE cycle_ts < UTC_TIMESTAMP() - INTERVAL :d DAY LIMIT 50000"),
            {"d": RETENTION_OUTCOMES_DAYS})
        out["outcomes_expired"] = r.rowcount
        await s.commit()
    return out


async def run_once() -> dict:
    global _backfilled
    t0 = time.monotonic()
    if not _backfilled:
        try:
            await backfill_price_history()
        except Exception:
            logger.exception("price backfill failed (non-fatal)")
        _backfilled = True
    oc = await compute_outcomes()
    st = await compute_stats()
    try:
        fa = await compute_flip_accuracy()
    except Exception:
        logger.exception("flip accuracy failed (non-fatal)")
        fa = {}
    ret = await _retention()
    stats = {"ts": datetime.utcnow().isoformat(), **oc, **st, **fa,
             "retention": ret,
             "duration_sec": round(time.monotonic() - t0, 1)}
    _last_run.clear()
    _last_run.update(stats)
    logger.info("smi_study nightly: %s", stats)
    return stats


async def _loop() -> None:
    await asyncio.sleep(FIRST_RUN_DELAY_SEC)
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("smi_study run failed — next attempt tomorrow")
        await asyncio.sleep(INTERVAL_SEC)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_event_loop().create_task(_loop())
        logger.info("smi_study started (nightly outcomes + stats; publish gate "
                    "n>=%d obs, span>=%dd)", PUBLISH_MIN_N, PUBLISH_MIN_DAYS)


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
