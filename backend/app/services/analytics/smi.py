"""Smart Money Index (ANALYTICS_BUILD_REPORT Phase 2.1).

Per asset, per sweep cycle, 0-100, computed from the CORE cohort rollups
(MM-excluded) + the unified flow stream. Runs at the end of each sweep cycle;
ZERO venue requests — DB only.

COMPONENT FORMULAS (weights are constants below; every component clamped to
[0, 100]; all raw inputs stored in `inputs` JSON so any number can be audited
down to this file):

  c1 POSITIONING SKEW (30%)
     s = notional_long / (notional_long + notional_short)   in [0,1]
     c1 = 100 * s
     (pure net-long share of cohort dollars; 50 = balanced book)

  c2 FLOW DIRECTION (25%)
     Signed 24h flow toward net-long from analytics_flow_events (both
     resolutions), using |notional_delta|:
       long  OPEN/INCREASE  -> +   long  CLOSE/REDUCE -> −
       short OPEN/INCREASE  -> −   short CLOSE/REDUCE -> +
       FLIP -> full |delta| signed by the NEW side (long +, short −)
     ratio = net_flow_24h / cohort_oi   (cohort_oi from the current rollup;
             0 flow or 0 OI -> ratio 0)
     c2 = 50 + 50 * clamp(ratio / FLOW_FULL_SCALE, -1, 1)
     FLOW_FULL_SCALE = 0.25 — flow equal to 25% of cohort OI in a day pins
     the component.

  c3 BREADTH (15%)
     sc = wallets_long / (wallets_long + wallets_short)     (count skew)
     sn = s from c1                                          (notional skew)
     c3 = 100 * (1 - |sc - sn|)
     (identical skews -> 100; three whales dragging notional away from the
     count majority -> penalized)

  c4 LEVERAGE APPETITE (15%)
     lev_now = wallet-count-weighted mean of avg_lev_long/avg_lev_short
     med30   = median of lev_now over this asset's trailing-30d core rollups
     c4 = clamp(50 * lev_now / med30, 0, 100)
     (at the median -> 50; 2x median risk-seeking -> 100; deleveraged -> low.
     med30 uses whatever history exists; < MIN_MEDIAN_CYCLES cycles -> c4 = 50
     neutral, flagged in inputs)

  c5 DIVERGENCE (15%)
     x = 2s - 1                       (cohort direction, -1..1)
     f = clamp(funding_hourly / FUNDING_FULL_SCALE, -1, 1)
         (HL positive funding = longs pay shorts = crowd is long)
     c5 = 50 + 50 * (-x * f)
     (cohort net-long while funding pays shorts (f<0): contrarian conviction
      -> >50; aligned with the paying crowd -> <50 = crowded)

  SMI = 0.30*c1 + 0.25*c2 + 0.15*c3 + 0.15*c4 + 0.15*c5

CALIBRATION HONESTY: flow and leverage components need trailing windows. The
API marks an asset "calibrating" until CALIBRATION_HOURS of SMI history
exists; the UI renders the marker — stability is never faked.
"""
import json
import time
from datetime import datetime, timedelta

from sqlalchemy import text

from app.db.database import get_session_factory
from app.utils.logger import get_logger

logger = get_logger(__name__)

W_SKEW = 0.30
W_FLOW = 0.25
W_BREADTH = 0.15
W_LEV = 0.15
W_DIV = 0.15
FLOW_FULL_SCALE = 0.25          # 24h net flow = 25% of cohort OI pins c2
FUNDING_FULL_SCALE = 0.0001     # 1bp/h pins the funding term
MIN_MEDIAN_CYCLES = 12          # < 12 rollup cycles (~4h) -> c4 neutral 50
CALIBRATION_HOURS = 48

BUILD = ("OPEN", "INCREASE")
CUT = ("CLOSE", "REDUCE")


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


async def compute_cycle(cycle_ts: datetime) -> int:
    """Compute + persist SMI for every asset with a core rollup at cycle_ts.
    Returns rows written. Idempotent (INSERT IGNORE on PK)."""
    t0 = time.monotonic()
    sf = get_session_factory()
    async with sf() as s:
        rollups = (await s.execute(text(
            "SELECT asset, wallets_long, wallets_short, notional_long, "
            " notional_short, avg_lev_long, avg_lev_short, cohort_oi, "
            " venue_funding FROM analytics_asset_rollups "
            "WHERE cycle_ts = :cy AND cohort_variant = 'core'"), {"cy": cycle_ts}
        )).mappings().all()
        if not rollups:
            return 0
        # 24h signed flow per asset, one grouped query (formula in module doc)
        flows = (await s.execute(text(
            "SELECT asset, SUM(CASE "
            " WHEN event_type IN ('OPEN','INCREASE') AND side='long'  THEN ABS(COALESCE(notional_delta,0)) "
            " WHEN event_type IN ('CLOSE','REDUCE')  AND side='long'  THEN -ABS(COALESCE(notional_delta,0)) "
            " WHEN event_type IN ('OPEN','INCREASE') AND side='short' THEN -ABS(COALESCE(notional_delta,0)) "
            " WHEN event_type IN ('CLOSE','REDUCE')  AND side='short' THEN ABS(COALESCE(notional_delta,0)) "
            " WHEN event_type = 'FLIP' AND side='long'  THEN ABS(COALESCE(notional_delta,0)) "
            " WHEN event_type = 'FLIP' AND side='short' THEN -ABS(COALESCE(notional_delta,0)) "
            " ELSE 0 END) net FROM analytics_flow_events "
            "WHERE detected_at >= :cut GROUP BY asset"),
            {"cut": cycle_ts - timedelta(hours=24)})).all()
        flow_by_asset = {a: float(n or 0) for a, n in flows}
        # trailing-30d leverage median per asset, computed in SQL: fetching the
        # raw 30d rollup rows (255K) decoded them on the event loop — the
        # watchdog blamed this fetch + the Python loop for the 2.6-3s stall
        # every sweep cycle (2026-09-28). Same lev_now/median/rounding
        # semantics as the old lev_now_of()/statistics.median path.
        lev_med = (await s.execute(text(
            "SELECT asset, MAX(cnt) cnt, ROUND(AVG(lev), 4) med FROM ("
            " SELECT asset, lev, "
            "  ROW_NUMBER() OVER (PARTITION BY asset ORDER BY lev) rn, "
            "  COUNT(*) OVER (PARTITION BY asset) cnt "
            " FROM ("
            "  SELECT asset, CASE "
            "   WHEN avg_lev_long IS NOT NULL AND wallets_long > 0 "
            "    AND avg_lev_short IS NOT NULL AND wallets_short > 0 "
            "    THEN ROUND((avg_lev_long * wallets_long + avg_lev_short * wallets_short) "
            "         / (wallets_long + wallets_short), 4) "
            "   WHEN avg_lev_long IS NOT NULL AND wallets_long > 0 "
            "    THEN ROUND(avg_lev_long, 4) "
            "   WHEN avg_lev_short IS NOT NULL AND wallets_short > 0 "
            "    THEN ROUND(avg_lev_short, 4) "
            "   ELSE NULL END AS lev "
            "  FROM analytics_asset_rollups "
            "  WHERE cohort_variant = 'core' AND cycle_ts >= :cut AND cycle_ts < :cy"
            " ) t WHERE lev IS NOT NULL"
            ") x WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2)) "
            "GROUP BY asset"),
            {"cut": cycle_ts - timedelta(days=30), "cy": cycle_ts})).all()

    hist_cnt_by_asset = {a: int(c) for a, c, _ in lev_med}
    med_by_asset = {a: float(m) for a, c, m in lev_med if m is not None}

    def lev_now_of(r) -> float | None:
        parts = []
        if r["avg_lev_long"] is not None and r["wallets_long"]:
            parts.append((float(r["avg_lev_long"]), r["wallets_long"]))
        if r["avg_lev_short"] is not None and r["wallets_short"]:
            parts.append((float(r["avg_lev_short"]), r["wallets_short"]))
        tot = sum(w for _, w in parts)
        return round(sum(v * w for v, w in parts) / tot, 4) if tot else None

    rows = []
    for r in rollups:
        nl = float(r["notional_long"])
        ns = float(r["notional_short"])
        oi = float(r["cohort_oi"])
        if nl + ns <= 0:
            continue
        s_share = nl / (nl + ns)
        c1 = _clamp(100.0 * s_share)

        net_flow = flow_by_asset.get(r["asset"], 0.0)
        ratio = (net_flow / oi) if oi > 0 else 0.0
        c2 = _clamp(50.0 + 50.0 * max(-1.0, min(1.0, ratio / FLOW_FULL_SCALE)))

        wl, ws_ = r["wallets_long"], r["wallets_short"]
        sc = wl / (wl + ws_) if (wl + ws_) else 0.5
        c3 = _clamp(100.0 * (1.0 - abs(sc - s_share)))

        lev_now = lev_now_of(r)
        hist_n = hist_cnt_by_asset.get(r["asset"], 0)
        med30 = med_by_asset.get(r["asset"]) if hist_n >= MIN_MEDIAN_CYCLES else None
        if lev_now is None or med30 is None or med30 <= 0:
            c4 = 50.0
        else:
            c4 = _clamp(50.0 * lev_now / med30)

        funding = float(r["venue_funding"]) if r["venue_funding"] is not None else None
        x = 2.0 * s_share - 1.0
        if funding is None:
            c5 = 50.0
        else:
            f = max(-1.0, min(1.0, funding / FUNDING_FULL_SCALE))
            c5 = _clamp(50.0 + 50.0 * (-x * f))

        smi = round(W_SKEW * c1 + W_FLOW * c2 + W_BREADTH * c3 + W_LEV * c4 + W_DIV * c5, 2)
        rows.append({
            "cy": cycle_ts, "a": r["asset"], "smi": smi,
            "c1": round(c1, 2), "c2": round(c2, 2), "c3": round(c3, 2),
            "c4": round(c4, 2), "c5": round(c5, 2),
            "inp": json.dumps({
                "notional_long": nl, "notional_short": ns, "cohort_oi": oi,
                "net_flow_24h": round(net_flow, 2), "flow_ratio": round(ratio, 6),
                "wallets_long": wl, "wallets_short": ws_,
                "count_skew": round(sc, 4), "notional_skew": round(s_share, 4),
                "lev_now": lev_now, "lev_median_30d": med30,
                "lev_hist_cycles": hist_n,
                "funding_hourly": funding,
            }),
        })

    if rows:
        sf = get_session_factory()
        async with sf() as s:
            await s.execute(text(
                "INSERT IGNORE INTO analytics_smi "
                "(cycle_ts, asset, smi, c1, c2, c3, c4, c5, inputs) "
                "VALUES (:cy, :a, :smi, :c1, :c2, :c3, :c4, :c5, :inp)"), rows)
            await s.commit()
    logger.info("smi: %d assets scored for cycle %s in %.2fs",
                len(rows), cycle_ts.isoformat(), time.monotonic() - t0)
    if rows:
        from app.services.alerts import producers as alert_producers
        await alert_producers.smi_cycle(cycle_ts, rows)
    return len(rows)


async def history_hours() -> float:
    """Span of stored SMI history — the calibration gate for the API/UI."""
    sf = get_session_factory()
    async with sf() as s:
        row = (await s.execute(text(
            "SELECT MIN(cycle_ts), MAX(cycle_ts) FROM analytics_smi"))).first()
    if not row or not row[0]:
        return 0.0
    return (row[1] - row[0]).total_seconds() / 3600.0
