"""Liquidation normalisers calibrated from history (spec v1.1 Part C, D-66).

Per coin, daily at 00:05 UTC and on the worker's first run:
  liq_5m_p90_long / liq_5m_p90_short
      90th percentile of same-side liquidation notional over rolling 5-minute
      windows (1-minute steps) across the trailing 180 days — non-zero windows
      only (a sparse tape makes the all-window p90 read 0). Falls back to the
      longest available window (>= 30 days) and needs >= 20 non-zero windows;
      below that the value is persisted as NULL with the counts and the models
      fall back to the fixed OI-fraction normalisers (logged).
  band_p80
      80th percentile of non-zero liquidation-map band notional over the same
      window, read from strat_liq_map_hist (the reconstructed 15m map, Part B).
  live_coverage
      live-feed notional / 0xArchive notional over the last 3 days (Part B);
      NULL when no archive rows exist. Live-feed inputs are scaled by
      1 / live_coverage (Snapshot.liq) so live and historical evaluations sit on
      the same scale. Recomputed weekly (Monday) once both feeds overlap.

Persisted to strat_calibration (coin, key, value, computed_at, sample_count,
window_days, note). Values are logged on every recompute.

Point-in-time history (spec v1.2 Part 4, D-76): strat_calibration_hist holds one
row per (coin, key, as_of day boundary) for liq_5m_p90_long/short and band_p80,
computed from data strictly BEFORE as_of over a trailing window of at least
MIN_WINDOW_DAYS and at most WINDOW_DAYS (NULL with a note when the trailing data
is shorter than the minimum). build_history() fills it for a span of days in
one pass; the live daily recompute appends the row for today; the replay looks
up the latest as_of <= boundary (load_as_of) and never sees future data.
"""
from __future__ import annotations

import datetime as dt
import logging
from bisect import bisect_left, bisect_right
from typing import Optional

import numpy as np
from sqlalchemy import text

logger = logging.getLogger("strategy_engine.calibration")

DAY_MS = 86_400_000
MIN_MS = 60_000
WINDOW_DAYS = 180
MIN_WINDOW_DAYS = 30
MIN_NONZERO_WINDOWS = 20
ROLL_MS = 5 * MIN_MS
STEP_MS = MIN_MS
COVERAGE_DAYS = 3
KEYS = ("liq_5m_p90_long", "liq_5m_p90_short", "band_p80", "live_coverage")

# the pre-v1.1 fixed normalisers, used ONLY when a calibrated value is unavailable
FALLBACK_OI_PCT = {"liq_5m_p90": 0.10, "band_p80": 0.10}


def rolling_5m_sums(ts_ms: list[int], notional: list[float]) -> np.ndarray:
    """Sum of notional in (t - 5min, t] for every minute step t that ends a
    non-empty window. Inputs sorted by ts."""
    if not ts_ms:
        return np.zeros(0)
    ts = np.asarray(ts_ms, dtype=np.int64)
    val = np.asarray(notional, dtype=np.float64)
    cum = np.concatenate([[0.0], np.cumsum(val)])
    # candidate window ends: every minute boundary from each fill's minute to +5 min
    ends = set()
    for t in ts:
        m = (int(t) // STEP_MS + 1) * STEP_MS
        for k in range(5):
            ends.add(m + k * STEP_MS)
    ends_arr = np.fromiter(sorted(ends), dtype=np.int64)
    lo = np.searchsorted(ts, ends_arr - ROLL_MS, side="right")
    hi = np.searchsorted(ts, ends_arr, side="right")
    sums = cum[hi] - cum[lo]
    return sums[sums > 0]


def percentile(values: np.ndarray, p: float) -> Optional[float]:
    if values.size == 0:
        return None
    return float(np.percentile(values, p))


async def _liq_rows(session, coin: str, start_ms: int, end_ms: int) -> tuple[list[int], list[float], list[str]]:
    rows = (await session.execute(text(
        "SELECT ts, side, notional FROM strat_liquidations WHERE coin=:c AND ts>:a AND ts<=:b "
        "AND notional IS NOT NULL ORDER BY ts"), {"c": coin, "a": start_ms, "b": end_ms})).all()
    return [int(r[0]) for r in rows], [float(r[2]) for r in rows], [str(r[1]) for r in rows]


async def _liq_span(session, coin: str) -> tuple[Optional[int], Optional[int]]:
    r = (await session.execute(text(
        "SELECT MIN(ts), MAX(ts) FROM strat_liquidations WHERE coin=:c"), {"c": coin})).first()
    return (int(r[0]) if r and r[0] else None, int(r[1]) if r and r[1] else None)


MAP_SOURCE_PRECEDENCE = ("levels", "live", "fills")   # D-69: position-based maps before the fills proxy


async def _band_notionals(session, coin: str, start_ms: int, end_ms: int) -> list[float]:
    """Band notionals for band_p80 over the window, ONE map source per boundary:
    0xArchive position snapshots ('levels') where they exist, else the live
    position map ('live'), else the forward-fills proxy ('fills'). Mixing the
    three constructions at the same boundary would blend different quantities."""
    rows = (await session.execute(text(
        "SELECT ts, source, notional FROM strat_liq_map_hist WHERE coin=:c AND ts>:a AND ts<=:b AND notional > 0"),
        {"c": coin, "a": start_ms, "b": end_ms})).all()
    best: dict[int, int] = {}
    rank = {s: i for i, s in enumerate(MAP_SOURCE_PRECEDENCE)}
    for ts, src, _n in rows:
        r = rank.get(str(src), len(rank))
        if r < best.get(int(ts), len(rank) + 1):
            best[int(ts)] = r
    return [float(n) for ts, src, n in rows if rank.get(str(src), len(rank)) == best[int(ts)]]


async def compute(session, coin: str, now_ms: int, window_days: int = WINDOW_DAYS) -> dict:
    """Computes every key for `coin`; returns {key: {value, sample_count, window_days, note}}."""
    first, _last = await _liq_span(session, coin)
    out: dict[str, dict] = {}
    if first is None:
        for k in KEYS:
            out[k] = {"value": None, "sample_count": 0, "window_days": 0.0, "note": "no liquidation rows"}
        return out
    avail_days = (now_ms - first) / DAY_MS
    win_days = min(window_days, avail_days)
    start_ms = now_ms - int(win_days * DAY_MS)
    note_win = "" if win_days >= window_days else f"window {win_days:.1f}d < {window_days}d (longest available)"
    below_min = win_days < MIN_WINDOW_DAYS
    ts, nv, sd = await _liq_rows(session, coin, start_ms, now_ms)
    for side in ("long", "short"):
        idx = [i for i, s in enumerate(sd) if s == side]
        sums = rolling_5m_sums([ts[i] for i in idx], [nv[i] for i in idx])
        n = int(sums.size)
        ok = n >= MIN_NONZERO_WINDOWS and not below_min
        note = "; ".join(x for x in (note_win,
                                     f"window below {MIN_WINDOW_DAYS}d minimum" if below_min else "",
                                     f"{n} non-zero windows < {MIN_NONZERO_WINDOWS}" if n < MIN_NONZERO_WINDOWS else "") if x)
        out[f"liq_5m_p90_{side}"] = {"value": percentile(sums, 90) if ok else None, "sample_count": n,
                                     "window_days": round(win_days, 2), "note": note or None}
    barr = np.asarray(await _band_notionals(session, coin, start_ms, now_ms), dtype=np.float64)
    nb = int(barr.size)
    ok_b = nb >= MIN_NONZERO_WINDOWS and not below_min
    note_b = "; ".join(x for x in (note_win,
                                   f"window below {MIN_WINDOW_DAYS}d minimum" if below_min else "",
                                   f"{nb} non-zero bands < {MIN_NONZERO_WINDOWS}" if nb < MIN_NONZERO_WINDOWS else "") if x)
    out["band_p80"] = {"value": percentile(barr, 80) if ok_b else None, "sample_count": nb,
                       "window_days": round(win_days, 2), "note": note_b or None}
    from app.strategy_engine.data.oxarchive import live_coverage
    cov = await live_coverage(session, coin, COVERAGE_DAYS, now_ms)
    out["live_coverage"] = {"value": cov["coverage_notional"], "sample_count": cov["archive_rows"],
                            "window_days": float(COVERAGE_DAYS),
                            "note": (None if cov["coverage_notional"] is not None else "no 0xArchive rows in the window")}
    return out


async def persist(session, coin: str, values: dict, now_ms: int) -> None:
    for k, d in values.items():
        await session.execute(text(
            "INSERT INTO strat_calibration (coin, `key`, value, computed_at, sample_count, window_days, note) "
            "VALUES (:c, :k, :v, :t, :n, :w, :note) ON DUPLICATE KEY UPDATE value=VALUES(value), "
            "computed_at=VALUES(computed_at), sample_count=VALUES(sample_count), window_days=VALUES(window_days), "
            "note=VALUES(note)"),
            {"c": coin, "k": k, "v": d.get("value"), "t": now_ms, "n": int(d.get("sample_count") or 0),
             "w": d.get("window_days"), "note": (d.get("note") or None)})
    await session.commit()


async def load(session, coin: str) -> dict:
    """{key: {value, sample_count, computed_at, window_days, note}} for the coin (empty when never computed)."""
    rows = (await session.execute(text(
        "SELECT `key`, value, computed_at, sample_count, window_days, note FROM strat_calibration WHERE coin=:c"),
        {"c": coin})).all()
    return {str(k): {"value": (float(v) if v is not None else None), "computed_at": int(t), "sample_count": int(n),
                     "window_days": (float(w) if w is not None else None), "note": note}
            for k, v, t, n, w, note in rows}


async def recompute(session, coins: list[str], now_ms: int, keys: Optional[tuple[str, ...]] = None) -> dict[str, dict]:
    """Compute + persist + log for every coin. `keys` restricts what is stored
    (weekly live_coverage refresh)."""
    result: dict[str, dict] = {}
    for coin in coins:
        vals = await compute(session, coin, now_ms)
        if keys:
            vals = {k: v for k, v in vals.items() if k in keys}
        await persist(session, coin, vals, now_ms)
        result[coin] = vals
    if not keys or any(k in HIST_KEYS for k in keys):
        # D-76: the point-in-time row for today (data strictly before 00:00 UTC)
        await build_history(session, coins, day_floor(now_ms), day_floor(now_ms), now_ms)
    for coin in coins:
        vals = result[coin]
        logger.info("calibration %s @ %s: %s", coin,
                    dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    ", ".join(f"{k}={_fmt(v['value'])} (n={v['sample_count']}, {v['window_days']}d"
                              f"{', ' + v['note'] if v.get('note') else ''})" for k, v in vals.items()))
    return result


# ---- point-in-time history (spec v1.2 Part 4, D-76) ---------------------------------
HIST_KEYS = ("liq_5m_p90_long", "liq_5m_p90_short", "band_p80")


def day_floor(ts_ms: int) -> int:
    return (int(ts_ms) // DAY_MS) * DAY_MS


def asof_values(liq_ts: list[int], liq_nv: list[float], liq_sd: list[str], band_ts: list[int], band_nv: list[float],
                first_ts: Optional[int], as_of_ms: int, window_days: int = WINDOW_DAYS) -> dict[str, dict]:
    """Pure: the three HIST_KEYS as of `as_of_ms` from pre-loaded rows (sorted by ts).
    Only rows with ts < as_of_ms are used; the window is min(window_days, days since
    the first liquidation row); below MIN_WINDOW_DAYS every value is NULL."""
    out: dict[str, dict] = {}
    if first_ts is None or first_ts >= as_of_ms:
        for k in HIST_KEYS:
            out[k] = {"value": None, "sample_count": 0, "window_days": 0.0, "note": "no liquidation rows before as_of"}
        return out
    avail_days = (as_of_ms - first_ts) / DAY_MS
    win_days = min(float(window_days), avail_days)
    start_ms = as_of_ms - int(win_days * DAY_MS)
    below_min = win_days < MIN_WINDOW_DAYS
    note_win = "" if win_days >= window_days else f"window {win_days:.1f}d < {window_days}d (longest available)"
    lo, hi = bisect_right(liq_ts, start_ms), bisect_left(liq_ts, as_of_ms)      # (start, as_of)
    for side in ("long", "short"):
        t = [liq_ts[i] for i in range(lo, hi) if liq_sd[i] == side]
        v = [liq_nv[i] for i in range(lo, hi) if liq_sd[i] == side]
        sums = rolling_5m_sums(t, v)
        n = int(sums.size)
        ok = n >= MIN_NONZERO_WINDOWS and not below_min
        note = "; ".join(x for x in (note_win, f"window below {MIN_WINDOW_DAYS}d minimum" if below_min else "",
                                     f"{n} non-zero windows < {MIN_NONZERO_WINDOWS}" if n < MIN_NONZERO_WINDOWS else "") if x)
        out[f"liq_5m_p90_{side}"] = {"value": percentile(sums, 90) if ok else None, "sample_count": n,
                                     "window_days": round(win_days, 2), "note": note or None}
    blo, bhi = bisect_right(band_ts, start_ms), bisect_left(band_ts, as_of_ms)
    barr = np.asarray(band_nv[blo:bhi], dtype=np.float64)
    nb = int(barr.size)
    ok_b = nb >= MIN_NONZERO_WINDOWS and not below_min
    note_b = "; ".join(x for x in (note_win, f"window below {MIN_WINDOW_DAYS}d minimum" if below_min else "",
                                   f"{nb} non-zero bands < {MIN_NONZERO_WINDOWS}" if nb < MIN_NONZERO_WINDOWS else "") if x)
    out["band_p80"] = {"value": percentile(barr, 80) if ok_b else None, "sample_count": nb,
                       "window_days": round(win_days, 2), "note": note_b or None}
    return out


async def _band_rows(session, coin: str, start_ms: int, end_ms: int) -> tuple[list[int], list[float]]:
    """(ts, notional) sorted by ts, one map source per boundary (same precedence as _band_notionals)."""
    rows = (await session.execute(text(
        "SELECT ts, source, notional FROM strat_liq_map_hist WHERE coin=:c AND ts>:a AND ts<:b AND notional > 0 ORDER BY ts"),
        {"c": coin, "a": start_ms, "b": end_ms})).all()
    rank = {s: i for i, s in enumerate(MAP_SOURCE_PRECEDENCE)}
    best: dict[int, int] = {}
    for ts, src, _n in rows:
        r = rank.get(str(src), len(rank))
        if r < best.get(int(ts), len(rank) + 1):
            best[int(ts)] = r
    keep = [(int(ts), float(n)) for ts, src, n in rows if rank.get(str(src), len(rank)) == best[int(ts)]]
    return [t for t, _ in keep], [n for _, n in keep]


async def build_history(session, coins: list[str], from_ms: int, to_ms: int, now_ms: Optional[int] = None,
                        window_days: int = WINDOW_DAYS) -> dict[str, dict]:
    """Persist HIST_KEYS for every UTC day boundary in [day_floor(from_ms), day_floor(to_ms)]
    per coin, each from data strictly before that boundary. Rows are loaded ONCE per
    coin and sliced per day. Returns {coin: {days, null_days, first_ok_day, last: {...}}}."""
    now_ms = now_ms or int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    d0, d1 = day_floor(from_ms), day_floor(to_ms)
    result: dict[str, dict] = {}
    for coin in coins:
        first, _last = await _liq_span(session, coin)
        span_start = d0 - window_days * DAY_MS - DAY_MS
        lts, lnv, lsd = await _liq_rows(session, coin, span_start, d1)
        bts, bnv = await _band_rows(session, coin, span_start, d1)
        days = null_days = 0
        first_ok = None
        last_vals: dict = {}
        d = d0
        batch: list[dict] = []
        while d <= d1:
            vals = asof_values(lts, lnv, lsd, bts, bnv, first, d, window_days)
            days += 1
            if any(v["value"] is None for v in vals.values()):
                null_days += 1
            elif first_ok is None:
                first_ok = d
            for k, v in vals.items():
                batch.append({"c": coin, "k": k, "a": d, "v": v["value"], "t": now_ms, "n": int(v["sample_count"] or 0),
                              "w": v["window_days"], "note": v.get("note") or None})
            last_vals = vals
            d += DAY_MS
        for i in range(0, len(batch), 500):
            await session.execute(text(
                "INSERT INTO strat_calibration_hist (coin, `key`, as_of, value, computed_at, sample_count, window_days, note) "
                "VALUES (:c, :k, :a, :v, :t, :n, :w, :note) ON DUPLICATE KEY UPDATE value=VALUES(value), "
                "computed_at=VALUES(computed_at), sample_count=VALUES(sample_count), window_days=VALUES(window_days), "
                "note=VALUES(note)"), batch[i:i + 500])
        await session.commit()
        result[coin] = {"days": days, "null_days": null_days, "first_ok_day": first_ok, "last": last_vals}
        logger.info("calibration history %s: %d days %s -> %s, %d with NULL, first complete day %s; last: %s", coin, days,
                    dt.datetime.fromtimestamp(d0 / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                    dt.datetime.fromtimestamp(d1 / 1000, dt.timezone.utc).strftime("%Y-%m-%d"), null_days,
                    dt.datetime.fromtimestamp(first_ok / 1000, dt.timezone.utc).strftime("%Y-%m-%d") if first_ok else "none",
                    ", ".join(f"{k}={_fmt(v['value'])} (n={v['sample_count']}, {v['window_days']}d)" for k, v in last_vals.items()))
    return result


async def load_as_of(session, coin: str, ts_ms: int) -> dict:
    """{key: {value, computed_at, sample_count, window_days, note, as_of}} from the latest
    history row with as_of <= ts_ms per key (empty when none exists before ts_ms)."""
    rows = (await session.execute(text(
        "SELECT h.`key`, h.value, h.computed_at, h.sample_count, h.window_days, h.note, h.as_of "
        "FROM strat_calibration_hist h JOIN ("
        "  SELECT `key` k, MAX(as_of) a FROM strat_calibration_hist WHERE coin=:c AND as_of<=:t GROUP BY `key`) m "
        "ON h.`key`=m.k AND h.as_of=m.a WHERE h.coin=:c"), {"c": coin, "t": ts_ms})).all()
    return {str(k): {"value": (float(v) if v is not None else None), "computed_at": int(t), "sample_count": int(n),
                     "window_days": (float(w) if w is not None else None), "note": note, "as_of": int(a)}
            for k, v, t, n, w, note, a in rows}


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "null"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.4f}"


def is_due(now_ms: int, last_ms: Optional[int]) -> bool:
    """Daily at 00:05 UTC (first boundary at/after it) and on first run."""
    if last_ms is None:
        return True
    d_now = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc)
    d_last = dt.datetime.fromtimestamp(last_ms / 1000, dt.timezone.utc)
    due_today = d_now.replace(hour=0, minute=5, second=0, microsecond=0)
    return d_now >= due_today and d_last < due_today


def coverage_due(now_ms: int, last_ms: Optional[int]) -> bool:
    """Weekly: first evaluation of the ISO week."""
    if last_ms is None:
        return True
    a = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc).isocalendar()[:2]
    b = dt.datetime.fromtimestamp(last_ms / 1000, dt.timezone.utc).isocalendar()[:2]
    return a != b
