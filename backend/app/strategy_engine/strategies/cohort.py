"""s03 cohort (doc 03 §4/§5) built from the metrics that exist TODAY.

Cohort = derived view over the sampled HL fill history (`hl_fill_events`), the
7d fill stats (`trader_fill_stats`), the analytics position snapshots
(`analytics_positions` / `analytics_wallet_state`) and the MM / hedger flags.
The doc's 90-day window is replaced by min(90d, available fill history) and
every cohort carries a label saying so ("cohort: 7d metrics · fill history 8d of
90d (90d at 2026-11-24)"). The window widens automatically as the sampler fills
in; nothing is invented and nothing waits.

Doc filters (verbatim): pnl/volume >= 0.5%, closed trades >= 40, max drawdown
<= 35%, avg leverage <= 10x, median hold >= 30 min. Score = 0.4·rank(pnl/vol) +
0.3·rank(pnl) + 0.3·rank(1/dd). Top 30. MM (clause A/B) and hedger wallets are
excluded (they have no directional view — doc 03 §11).
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Optional

from sqlalchemy import bindparam, text

logger = logging.getLogger("strategy_engine.cohort")

_DAY = 86_400_000
WINDOW_DAYS = 90
TOP_N = 30
CACHE_SEC = 600

# doc 03 §4 filters
MIN_PNL_TO_VOL = 0.005
MIN_TRADES = 40
MAX_DD = 0.35
MAX_LEV = 10.0
MIN_HOLD_MIN = 30.0

# MM clause A/B (same constants as analytics.cohort — mirrored, not imported)
MM_MAKER_RATIO = 0.6
MM_TRADES_7D = 500
MM_NOTIONAL_X_AV = 25.0
MM_N_ASSETS = 8

_cache: dict = {"ts": 0, "cohort": None}


def _q(sql: str):
    """text() with the wallet list bound as an expanding IN (:wl)."""
    return text(sql).bindparams(bindparam("wl", expanding=True))


def _rank(values: list[float]) -> list[float]:
    """Percentile rank 0..1 (ties share the lower rank)."""
    n = len(values)
    if n <= 1:
        return [1.0] * n
    order = sorted(values)
    out = []
    for v in values:
        below = sum(1 for x in order if x < v)
        out.append(below / (n - 1))
    return out


async def build_cohort(s, now_ms: Optional[int] = None) -> dict:
    """Returns {'wallets': [...], 'label', 'window_days', 'fill_start_ms',
    'full_history_at', 'candidates', 'excluded_mm', 'excluded_hedger', 'built_ts'}."""
    now_ms = now_ms or int(time.time() * 1000)
    if _cache["cohort"] and now_ms - _cache["ts"] < CACHE_SEC * 1000:
        return _cache["cohort"]

    first = (await s.execute(text("SELECT MIN(ts_ms) FROM hl_fill_events"))).scalar()
    fill_start = int(first) if first else now_ms
    have_days = max(0.0, (now_ms - fill_start) / _DAY)
    win_days = min(WINDOW_DAYS, have_days)
    start_ms = max(fill_start, now_ms - WINDOW_DAYS * _DAY)
    full_at = _dt.datetime.fromtimestamp((fill_start + WINDOW_DAYS * _DAY) / 1000.0, tz=_dt.timezone.utc)

    # per-wallet aggregates over the window (closing fills = round-trip proxy)
    agg = (await s.execute(text(
        "SELECT wallet_address w, COALESCE(SUM(closed_pnl),0) pnl, COALESCE(SUM(px*sz),0) vol, "
        " SUM(CASE WHEN dir LIKE 'Close%' OR dir LIKE '%>%' THEN 1 ELSE 0 END) trades, COUNT(*) fills "
        "FROM hl_fill_events WHERE ts_ms >= :a GROUP BY wallet_address HAVING trades >= :t"),
        {"a": start_ms, "t": MIN_TRADES})).mappings().all()
    rows = {r["w"]: dict(r) for r in agg}
    if not rows:
        out = {"wallets": [], "label": f"cohort: 7d metrics · fill history {have_days:.0f}d of {WINDOW_DAYS}d — no wallet with >= {MIN_TRADES} closing fills yet",
               "window_days": win_days, "fill_start_ms": fill_start, "full_history_at": full_at.strftime("%Y-%m-%d"),
               "candidates": 0, "excluded_mm": 0, "excluded_hedger": 0, "built_ts": now_ms}
        _cache.update(ts=now_ms, cohort=out)
        return out
    wl = list(rows)

    # max drawdown of the realized-pnl equity curve, 1h resolution (hourly pnl
    # buckets → cumulative peak-to-trough in Python). A window-function version
    # over the raw fills held a table-level lock for 20+ minutes on MyISAM.
    ddrows = (await s.execute(_q(
        "SELECT wallet_address w, FLOOR(ts_ms/3600000) h, SUM(closed_pnl) p FROM hl_fill_events "
        "WHERE ts_ms >= :a AND closed_pnl <> 0 AND wallet_address IN :wl GROUP BY wallet_address, h ORDER BY wallet_address, h"),
        {"a": start_ms, "wl": tuple(wl)})).all()
    cum: dict[str, float] = {}
    peak: dict[str, float] = {}
    for w, _h, p in ddrows:
        c = cum.get(w, 0.0) + float(p or 0.0)
        cum[w] = c
        pk = max(peak.get(w, 0.0), c)
        peak[w] = pk
        rows[w]["max_dd"] = max(rows[w].get("max_dd", 0.0), pk - c)

    # account value (latest state row), 7d stats (hold, maker ratio, dd% fallback)
    st = (await s.execute(_q(
        "SELECT s.wallet, s.account_value, s.gross_notional, s.n_assets FROM analytics_wallet_state s "
        "JOIN (SELECT wallet, MAX(cycle_ts) mc FROM analytics_wallet_state WHERE wallet IN :wl GROUP BY wallet) m "
        " ON m.wallet=s.wallet AND m.mc=s.cycle_ts"), {"wl": tuple(wl)})).mappings().all()
    state = {r["wallet"]: dict(r) for r in st}
    fs = (await s.execute(_q(
        "SELECT wallet_address w, trades_7d, avg_hold_minutes, maker_ratio, max_drawdown_pct_7d "
        "FROM trader_fill_stats WHERE wallet_address IN :wl"), {"wl": tuple(wl)})).mappings().all()
    stats = {r["w"]: dict(r) for r in fs}
    hf = (await s.execute(_q(
        "SELECT wallet FROM analytics_wallet_flags WHERE is_likely_hedger=1 AND wallet IN :wl"), {"wl": tuple(wl)})).all()
    hedgers = {r[0] for r in hf}
    lv = (await s.execute(_q(
        "SELECT wallet, AVG(leverage) lev FROM analytics_positions "
        "WHERE cycle_ts=(SELECT MAX(cycle_ts) FROM analytics_positions) AND wallet IN :wl AND leverage IS NOT NULL GROUP BY wallet"),
        {"wl": tuple(wl)})).mappings().all()
    lev = {r["wallet"]: float(r["lev"]) for r in lv}

    cands = []
    ex_mm = ex_h = 0
    for w, r in rows.items():
        f = stats.get(w) or {}
        a = state.get(w) or {}
        if w in hedgers:
            ex_h += 1
            continue
        mm_a = (f.get("maker_ratio") is not None and float(f["maker_ratio"]) >= MM_MAKER_RATIO
                and int(f.get("trades_7d") or 0) >= MM_TRADES_7D)
        av = float(a["account_value"]) if a.get("account_value") else 0.0
        mm_b = av > 0 and float(a.get("gross_notional") or 0) >= MM_NOTIONAL_X_AV * av and int(a.get("n_assets") or 0) >= MM_N_ASSETS
        if mm_a or mm_b:
            ex_mm += 1
            continue
        pnl = float(r["pnl"]); vol = float(r["vol"]); trades = int(r["trades"])
        p2v = pnl / vol if vol > 0 else 0.0
        max_dd = float(r.get("max_dd") or 0.0)
        if av > 0:
            dd_pct = max_dd / av
        elif f.get("max_drawdown_pct_7d") is not None:
            dd_pct = float(f["max_drawdown_pct_7d"])
        else:
            dd_pct = None
        hold = float(f["avg_hold_minutes"]) if f.get("avg_hold_minutes") is not None else None
        l = lev.get(w)
        m = {"pnl": pnl, "vol": vol, "pnl_to_vol": p2v, "trades": trades, "max_dd": max_dd, "dd_pct": dd_pct,
             "hold_min": hold, "lev": l, "account_value": av or None}
        if p2v < MIN_PNL_TO_VOL or trades < MIN_TRADES:
            continue
        if dd_pct is not None and dd_pct > MAX_DD:
            continue
        if l is not None and l > MAX_LEV:
            continue
        if hold is not None and hold < MIN_HOLD_MIN:
            continue
        cands.append((w, m))

    if cands:
        r1 = _rank([m["pnl_to_vol"] for _, m in cands])
        r2 = _rank([m["pnl"] for _, m in cands])
        r3 = _rank([1.0 / max(m["dd_pct"] if m["dd_pct"] is not None else (m["max_dd"] / max(abs(m["pnl"]), 1.0)), 1e-6) for _, m in cands])
        scored = [(w, m, 0.4 * a + 0.3 * b + 0.3 * c) for (w, m), a, b, c in zip(cands, r1, r2, r3)]
        scored.sort(key=lambda x: -x[2])
    else:
        scored = []
    wallets = [{"wallet": w, "score": round(sc, 4), "metrics": m} for w, m, sc in scored[:TOP_N]]
    label = (f"cohort: 7d metrics · fill history {have_days:.0f}d of {WINDOW_DAYS}d "
             f"({WINDOW_DAYS}d at ~{full_at:%Y-%m-%d}) · {len(wallets)} wallets of {len(cands)} passing / {len(rows)} candidates")
    out = {"wallets": wallets, "label": label, "window_days": win_days, "fill_start_ms": fill_start,
           "full_history_at": full_at.strftime("%Y-%m-%d"), "candidates": len(rows), "passing": len(cands),
           "excluded_mm": ex_mm, "excluded_hedger": ex_h, "built_ts": now_ms}
    _cache.update(ts=now_ms, cohort=out)
    logger.info("cohort built: %s", label)
    return out


def _empty_signal(cohort: dict) -> dict:
    return {"net_dir": None, "fresh_agree": 0, "fresh_dir": None, "cohort_vwap": None, "n_long": 0, "n_short": 0,
            "long_notional": 0.0, "short_notional": 0.0, "fresh_long": 0, "fresh_short": 0, "positions_cycle_ts": None,
            "fresh_wallets": [], "label": cohort.get("label", "")}


def signal_from_rows(cohort: dict, pos_rows, ev_rows) -> dict:
    """Pure core of cohort_signal — also used by the backtest replay (as-of rows).
    pos_rows: [{wallet, side, notional, cycle_ts}] of ONE position snapshot;
    ev_rows: [{wallet, event_type, side, size_before, size_after, notional_delta, ref_px}]
    flow events of the trailing 60 min (OPEN/INCREASE/FLIP), oldest first."""
    out = _empty_signal(cohort)
    ln = sn = 0.0
    for r in pos_rows:
        n = float(r["notional"] or 0)
        if str(r["side"]).lower().startswith("l"):
            ln += n; out["n_long"] += 1
        else:
            sn += n; out["n_short"] += 1
        ct = r["cycle_ts"]
        out["positions_cycle_ts"] = ct.isoformat() if hasattr(ct, "isoformat") else str(ct)
    out["long_notional"], out["short_notional"] = ln, sn
    if ln + sn > 0:
        out["net_dir"] = (ln - sn) / (ln + sn)
    fresh: dict[str, dict] = {}
    for r in ev_rows:
        side = "long" if str(r["side"]).lower().startswith("l") else "short"
        if r["event_type"] == "INCREASE":
            sb = float(r["size_before"] or 0); sa = float(r["size_after"] or 0)
            if sb <= 0 or (sa - sb) / sb < 0.25:
                continue
        nd = abs(float(r["notional_delta"] or 0))
        px = float(r["ref_px"]) if r["ref_px"] else None
        f = fresh.setdefault(r["wallet"], {"side": side, "notional": 0.0, "pxn": 0.0})
        f["side"] = side
        if px and nd > 0:
            f["notional"] += nd; f["pxn"] += px * nd
    fl = [w for w, f in fresh.items() if f["side"] == "long"]
    fsd = [w for w, f in fresh.items() if f["side"] == "short"]
    out["fresh_long"], out["fresh_short"] = len(fl), len(fsd)
    if fl or fsd:
        d = "long" if len(fl) >= len(fsd) else "short"
        ws = fl if d == "long" else fsd
        out["fresh_dir"] = d
        out["fresh_agree"] = len(ws)
        out["fresh_wallets"] = ws
        tn = sum(fresh[w]["notional"] for w in ws)
        if tn > 0:
            out["cohort_vwap"] = sum(fresh[w]["pxn"] for w in ws) / tn
    return out


async def cohort_signal(s, cohort: dict, coin: str, now_ms: int) -> dict:
    """Per-coin cohort signal (doc 03 §5): net_dir from the latest position
    snapshot, fresh_agree / cohort_vwap from the flow events of the last 60 min."""
    wl = [w["wallet"] for w in cohort.get("wallets", [])]
    if not wl:
        return _empty_signal(cohort)
    # D-101: the latest cycle FOR THIS ASSET, not the global MAX(cycle_ts). The
    # sweep writes ~2,700 rows per cycle asset by asset, so a read that lands
    # mid-write saw a global max whose rows for this coin did not exist yet and
    # returned an empty position set — net_dir None, every cohort reason 0.
    pos = (await s.execute(_q(
        "SELECT wallet, side, notional, cycle_ts FROM analytics_positions "
        "WHERE asset=:c AND wallet IN :wl AND cycle_ts=("
        "  SELECT MAX(cycle_ts) FROM analytics_positions WHERE asset=:c)"),
        {"c": coin, "wl": tuple(wl)})).mappings().all()
    since = _dt.datetime.fromtimestamp((now_ms - 3_600_000) / 1000.0, tz=_dt.timezone.utc).replace(tzinfo=None)
    ev = (await s.execute(_q(
        "SELECT wallet, event_type, side, size_before, size_after, notional_delta, ref_px FROM analytics_flow_events "
        "WHERE asset=:c AND detected_at >= :t AND wallet IN :wl AND event_type IN ('OPEN','INCREASE','FLIP') "
        "ORDER BY detected_at"), {"c": coin, "t": since, "wl": tuple(wl)})).mappings().all()
    return signal_from_rows(cohort, pos, ev)
