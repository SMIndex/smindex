"""Backtest replay driver — candle-driven replay of the SAME strategy classes
over the backfilled 15m/1h/4h candles (strat_candles), with the derived layers
(funding gauge, regime, bias) rebuilt as-of each bar from the rows that exist
(strat_funding, strat_events) and the sub-candle feeds (OI-1m, trades-1m, book-5s,
liquidations, analytics positions/flows) supplied only where they exist.

Coverage-honest, never blocking: bars before a feed's coverage evaluate with those
conditions labelled "unavailable (not in score)" (exactly like the live
evaluator) and the report header states each feed's coverage over the range.

Fills are simulated on the 15m OHLC that FOLLOWS the signal bar (entry when the
bar trades through the limit; stop before target when both are hit in one bar;
time stop at the bar close; one open position per strategy/coin). Fees use the
same maker/taker schedule as the paper executor (compute_close).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import threading
import time
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

from ..features import bias as bias_mod
from ..features import funding_gauge as fg
from ..features import regime as regime_mod
from ..features import timing as timing_mod
from ..execution.manager import compute_close
from ..risk.engine import size_for
from ..strategies import common as cm
from ..strategies import cohort as cohort_mod
from ..strategies.base import StrategyContext
from .metrics import compute_metrics

# Set by the worker on shutdown: replay_coin runs in a thread that
# asyncio.run() joins at exit, so a 10-minute replay must bail out
# cooperatively or systemd SIGKILLs the worker after its stop timeout
# (prod 2026-09-03 21:33 CEST: "Failed with result 'timeout'").
STOP = threading.Event()


class ReplayAborted(RuntimeError):
    pass

logger = logging.getLogger("strategy_engine.backtest")

RESULTS_DIR = Path(__file__).resolve().parents[3] / "backtest" / "results"
CACHE_MAX_AGE_S = 24 * 3600
_BAR = 900_000
_H = 3_600_000
_DAY = 86_400_000

# Series each strategy needs beyond candles (coverage header + honesty label).
REQUIRED = {
    "s05_session_open": ["strat_candles", "strat_trades_1m"],
    "s05c_session_open": ["strat_candles", "strat_trades_1m"],
    "s01_liq_sweep": ["strat_liquidations", "strat_trades_1m", "strat_candles", "strat_book_5s"],
    "s04_vol_compression": ["strat_candles", "strat_oi_1m", "strat_book_5s"],
    "s02_funding_flow": ["strat_funding", "strat_oi_1m", "strat_candles"],
    "s03_whale_follow": ["strat_candles", "analytics_positions", "analytics_flow_events"],
    "s06_hull_fisher_ema": ["strat_candles", "strat_oi_1m", "strat_funding"],
    "s06u_hull_fisher_ema": ["strat_candles"],
}
_TS_COL = {"analytics_positions": "cycle_ts", "analytics_flow_events": "detected_at"}


def _iso(ms: int | None) -> str:
    if ms is None:
        return "n/a"
    return _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _dt_ms(v) -> int:
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    return int(v.replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


async def coverage_start(session, table: str) -> int | None:
    col = _TS_COL.get(table, "ts")
    r = (await session.execute(text(f"SELECT MIN({col}) FROM {table}"))).scalar()
    return _dt_ms(r) if r is not None else None


# --------------------------------------------------------------------------- data

async def load_series(session, coin: str, start_ms: int, end_ms: int, need_cohort: bool) -> dict:
    """Everything the replay needs for one coin, loaded once (oldest first)."""
    async def rows(sql, **kw):
        return [dict(r) for r in (await session.execute(text(sql), {"c": coin, **kw})).mappings().all()]
    warm_from = start_ms - 3000 * _BAR          # 31d of 15m for doc-05 RV24h pct(30d); strategies still see the last 300 bars
    d = {
        "c15": await rows("SELECT ts,o,h,l,c,v FROM strat_candles WHERE coin=:c AND tf='15m' AND ts BETWEEN :a AND :b ORDER BY ts", a=warm_from, b=end_ms),
        "c1h": await rows("SELECT ts,o,h,l,c,v FROM strat_candles WHERE coin=:c AND tf='1h' AND ts BETWEEN :a AND :b ORDER BY ts", a=start_ms - 400 * _H, b=end_ms),
        "c4h": await rows("SELECT ts,o,h,l,c,v FROM strat_candles WHERE coin=:c AND tf='4h' AND ts BETWEEN :a AND :b ORDER BY ts", a=start_ms - 400 * 4 * _H, b=end_ms),
        "fund_hl": await rows("SELECT ts,predicted_rate FROM strat_funding WHERE coin=:c AND venue='hl' AND predicted_rate IS NOT NULL AND ts BETWEEN :a AND :b ORDER BY ts", a=start_ms - 30 * _DAY, b=end_ms),
        "fund_bn": await rows("SELECT ts,rate FROM strat_funding WHERE coin=:c AND venue='binance' AND rate IS NOT NULL AND ts BETWEEN :a AND :b ORDER BY ts", a=start_ms - _DAY, b=end_ms),
        "events": [int(r["utc_ts"]) for r in await rows("SELECT utc_ts FROM strat_events WHERE utc_ts BETWEEN :a AND :b ORDER BY utc_ts", a=start_ms - _DAY, b=end_ms + _DAY)],
        "oi": await rows("SELECT ts,oi_notional,funding,mark,oracle FROM strat_oi_1m WHERE coin=:c AND ts BETWEEN :a AND :b ORDER BY ts", a=start_ms - 25 * _H, b=end_ms),
        "tr": await rows("SELECT ts,taker_buy_notional,taker_sell_notional,count FROM strat_trades_1m WHERE coin=:c AND ts BETWEEN :a AND :b ORDER BY ts", a=start_ms - 7 * _H, b=end_ms),
        "bk": await rows("SELECT ts,mid,bid_0_1,bid_0_3,bid_0_5,ask_0_1,ask_0_3,ask_0_5 FROM strat_book_5s WHERE coin=:c AND ts BETWEEN :a AND :b ORDER BY ts", a=start_ms - 4 * _H, b=end_ms),
        "liq": await rows("SELECT ts,side,px,sz,notional,liquidated_user,method,coverage FROM strat_liquidations WHERE coin=:c AND ts BETWEEN :a AND :b ORDER BY ts", a=start_ms - _H, b=end_ms),
        "pos": [], "flows": [], "cohort": None, "_derived": {},
    }
    if need_cohort:
        cohort = await cohort_mod.build_cohort(session, end_ms)
        d["cohort"] = cohort
        wl = [w["wallet"] for w in cohort.get("wallets", [])]
        if wl:
            q = cohort_mod._q
            a_dt = _dt.datetime.fromtimestamp(start_ms / 1000.0, tz=_dt.timezone.utc).replace(tzinfo=None)
            b_dt = _dt.datetime.fromtimestamp(end_ms / 1000.0, tz=_dt.timezone.utc).replace(tzinfo=None)
            pos = (await session.execute(q(
                "SELECT wallet, side, notional, cycle_ts FROM analytics_positions WHERE asset=:c AND wallet IN :wl "
                "AND cycle_ts BETWEEN :a AND :b ORDER BY cycle_ts"), {"c": coin, "wl": tuple(wl), "a": a_dt, "b": b_dt})).mappings().all()
            d["pos"] = [dict(r) for r in pos]
            ev = (await session.execute(q(
                "SELECT wallet, event_type, side, size_before, size_after, notional_delta, ref_px, detected_at "
                "FROM analytics_flow_events WHERE asset=:c AND wallet IN :wl AND event_type IN ('OPEN','INCREASE','FLIP') "
                "AND detected_at BETWEEN :a AND :b ORDER BY detected_at"), {"c": coin, "wl": tuple(wl), "a": a_dt, "b": b_dt})).mappings().all()
            d["flows"] = [dict(r) for r in ev]
    return d


class _Cursor:
    """Monotone 'rows with ts <= t' view over a sorted list (O(1) amortised)."""

    def __init__(self, rows: list[dict], key: str = "ts", to_ms=None):
        self.rows = rows
        self.key = key
        self.to_ms = to_ms or (lambda v: int(v))
        self.i = 0

    def upto(self, t: int, last: int | None = None) -> list[dict]:
        while self.i < len(self.rows) and self.to_ms(self.rows[self.i][self.key]) <= t:
            self.i += 1
        if last is None:
            return self.rows[: self.i]
        return self.rows[max(0, self.i - last): self.i]


# ------------------------------------------------------------------------ replay

def _derived(t: int, d: dict, cur: dict, closes_15m: list[float], closes_1h: list[float], oi_upto: list[dict]) -> tuple[dict, dict, dict]:
    """Gauge / regime / bias rebuilt as-of bar t from the rows that exist."""
    fh = cur["fund_hl"].upto(t)
    hist = [float(r["predicted_rate"]) * 8.0 for r in fh if r["ts"] >= t - 30 * _DAY]
    hlf = float(fh[-1]["predicted_rate"]) if fh else None
    bn = cur["fund_bn"].upto(t, last=1)
    g = fg.compute_gauge(hlf, hist, float(bn[-1]["rate"]) if bn else None)
    gauge = {"ts": t, **g}
    rv_ratio, _w = cm.rv_1h_vs_24h(closes_15m[-100:])
    chg, _have, _w2 = cm.oi_change(oi_upto, 24 * _H, t)
    f_ext = min(1.0, abs(g["funding_z"]) / 2.0) if g["funding_z"] is not None else None
    r = regime_mod.evaluate(t, event_ts_list=[e for e in d["events"] if t - 30 * 60_000 <= e <= t + 2 * _H],
                            last_oracle_ms=t,           # replay: oracle assumed fresh at the bar close
                            rv_vs_24h_avg=rv_ratio, oi_24h_change_mag=(abs(chg) if chg is not None else None),
                            funding_extremity=f_ext)
    regime = {"ts": t, "allowed": r["allowed"], "score": r["score"], "blockers": r["blockers"]}
    hd = timing_mod.hull_direction(closes_1h, 21) if len(closes_1h) >= 3 else None
    b = bias_mod.compute_bias(funding_tilt=g["tilt"], hull_1h_dir=hd)
    bias = {"ts": t, "score": b["bias_score"], "bias_score": b["bias_score"], "components": b["components"]}
    return gauge, regime, bias


def _session_of(ms: int) -> str:
    h = _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.timezone.utc).hour
    return "Asia (00-07)" if h < 7 else "EU (07-13)" if h < 13 else "US (13-21)" if h < 21 else "Late (21-24)"


def replay_coin(strategy, coin: str, d: dict, start_ms: int, end_ms: int, params: dict, equity: float = 1000.0) -> dict:
    """Pure CPU replay for one strategy/coin. Returns trades, evaluation stats,
    per-condition unavailable counts and warming counts."""
    c15 = d["c15"]
    cur = {k: _Cursor(d[k]) for k in ("c1h", "c4h", "fund_hl", "fund_bn", "oi", "tr", "bk", "liq")}
    cur["pos"] = _Cursor(d["pos"], "cycle_ts", _dt_ms)
    cur["flows"] = _Cursor(d["flows"], "detected_at", _dt_ms)
    funding_hours: set[int] = set()
    fh_i = 0
    trades: list[dict] = []
    open_pos: dict | None = None
    pending: dict | None = None
    stats = {"evals": 0, "fires": 0, "fires_skipped_open": 0, "entries_expired": 0, "entries_taker": 0, "warming_evals": 0,
             "unavailable": defaultdict(int), "warming_conds": defaultdict(int), "met": defaultdict(int),
             "evaluated": defaultdict(int), "not_allowed": 0}
    last_minute_key = None
    for i, bar in enumerate(c15):
        t = int(bar["ts"])
        if t < start_ms:
            continue
        if STOP.is_set():
            raise ReplayAborted(f"{strategy.id} {coin}: worker stopping")
        # ---- manage the simulated position on THIS bar (signal came from an earlier bar)
        h, l, c = float(bar["h"]), float(bar["l"]), float(bar["c"])
        if pending is not None:
            if t > pending["expires"]:
                stats["entries_expired"] += 1
                pending = None
            else:
                px = pending["entry_px"]
                through = (l < px) if pending["direction"] == "long" else (h > px)
                if through:
                    open_pos = {**pending, "fill_ts": t, "mae": 0.0, "mfe": 0.0}
                    pending = None
                elif pending.get("chase"):
                    # s05c chase: live it re-quotes 3x over ~2 min then crosses; at 15m
                    # resolution the nearest honest price is this bar's open (taker fee)
                    open_pos = {**pending, "entry_px": float(bar["o"]), "entry_taker": True,
                                "fill_ts": t, "mae": 0.0, "mfe": 0.0}
                    stats["entries_taker"] += 1
                    pending = None
        if open_pos is not None and open_pos["fill_ts"] < t:
            e, st, tg, dirn = open_pos["entry_px"], open_pos["stop_px"], open_pos["target_px"], open_pos["direction"]
            adverse = (e - l) / e if dirn == "long" else (h - e) / e
            favor = (h - e) / e if dirn == "long" else (e - l) / e
            open_pos["mae"] = max(open_pos["mae"], adverse)
            open_pos["mfe"] = max(open_pos["mfe"], favor)
            hit_stop = (l <= st) if dirn == "long" else (h >= st)
            hit_tgt = tg is not None and ((h >= tg) if dirn == "long" else (l <= tg))
            exit_px = None; reason = None
            if hit_stop:                        # stop before target when both trade in one bar
                exit_px, reason = st, "stop"
            elif hit_tgt:
                exit_px, reason = tg, "target"
            elif t - open_pos["fill_ts"] >= open_pos["hold_ms"]:
                exit_px, reason = c, "time_stop"
            if exit_px is not None:
                g_, fees, net = compute_close(dirn, e, exit_px, open_pos["size"], reason,
                                              entry_taker=bool(open_pos.get("entry_taker")))
                trades.append({**open_pos, "exit_ts": t, "exit_px": exit_px, "exit_reason": reason,
                               "pnl_gross": g_, "fees": fees, "pnl_net": net})
                open_pos = None
        # ---- evaluate at the bar close
        if strategy.cadence == "minute":
            key = t // 60_000
            if key == last_minute_key:
                continue
            last_minute_key = key
        candles = c15[max(0, i - 299): i + 1]
        closes = [float(x["c"]) for x in candles]
        ohlc_30d = c15[max(0, i - 2999): i + 1]
        closes_30d = [float(x["c"]) for x in ohlc_30d]
        c1h = cur["c1h"].upto(t, last=60)
        c4h = cur["c4h"].upto(t, last=60)
        oi = cur["oi"].upto(t, last=1500)
        tr = cur["tr"].upto(t, last=400)
        bk = cur["bk"].upto(t, last=2200)
        liqs = [r for r in cur["liq"].upto(t) if r["ts"] >= t - 10 * 60_000]
        while fh_i < len(d["fund_hl"]) and d["fund_hl"][fh_i]["ts"] <= t:
            funding_hours.add(d["fund_hl"][fh_i]["ts"] // _H)
            fh_i += 1
        dc = d.setdefault("_derived", {})
        if t in dc:
            gauge, regime, bias = dc[t]
        else:
            gauge, regime, bias = _derived(t, d, cur, closes, [float(x["c"]) for x in cur["c1h"].upto(t)], oi)
            dc[t] = (gauge, regime, bias)
        coh = None
        if d["cohort"] is not None:
            pos_all = cur["pos"].upto(t)
            pos_rows = []
            if pos_all:
                last_cycle = pos_all[-1]["cycle_ts"]
                pos_rows = [r for r in pos_all if r["cycle_ts"] == last_cycle and _dt_ms(last_cycle) >= t - 2 * _H]
            ev_rows = [r for r in cur["flows"].upto(t) if _dt_ms(r["detected_at"]) >= t - _H]
            feed_started = bool(pos_all) or bool(cur["flows"].upto(t))
            if feed_started:                 # before the analytics feed exists the cohort is unavailable
                sig = cohort_mod.signal_from_rows(d["cohort"], pos_rows, ev_rows)
                coh = {**sig, "n_wallets": len(d["cohort"].get("wallets", [])), "label": d["cohort"].get("label", "")}
        ctx = StrategyContext(coin=coin, now_ms=t, params=params, candles_15m=candles, closes_15m_30d=closes_30d, ohlc_15m_30d=ohlc_30d, candles_1h=c1h, candles_4h=c4h,
                              oi_1m=oi, trades_1m=tr, book_5s=bk, book_last=(bk[-1] if bk else None),
                              gauge=gauge, regime=regime, bias=bias, liquidations=liqs,
                              liq_coverage=(liqs[-1].get("coverage") or "partial") if liqs else "partial",
                              liq_clusters=[], cohort=coh, funding_rows=len(funding_hours))
        res = strategy.evaluate(ctx)
        stats["evals"] += 1
        for n in res.not_evaluated:
            stats["unavailable"][n] += 1
        for cnd in res.conditions:
            if cnd.met is not None:
                stats["evaluated"][cnd.name] += 1
                if cnd.met:
                    stats["met"][cnd.name] += 1
        for w in res.warming:
            name, _, lab = w.partition(": ")
            if lab.startswith("warming"):
                stats["warming_conds"][name] += 1
        if res.warming:
            stats["warming_evals"] += 1
        if not regime["allowed"]:
            stats["not_allowed"] += 1
        if res.fired and res.intents:
            stats["fires"] += 1
            if open_pos is not None or pending is not None:
                stats["fires_skipped_open"] += 1
                continue
            intent = next((x for x in res.intents if x.kind == "entry"), None)
            if intent is None or not intent.px:
                continue
            stop = intent.meta.get("stop", intent.px)
            sized = size_for(intent.px, stop, equity)
            mult = float(intent.meta.get("size_mult", 1.0) or 1.0)
            ttl = int(intent.meta.get("entry_valid_min") or int(intent.meta.get("time_stop_h", 3)) * 60)
            pending = {"strategy": strategy.id, "asset": coin, "direction": res.direction, "signal_ts": t,
                       "entry_px": float(intent.px), "stop_px": float(stop), "target_px": intent.meta.get("target1"),
                       "size": sized.size * mult, "leverage": sized.leverage, "score": res.total_score,
                       "hold_ms": int(intent.meta.get("time_stop_h", 3)) * _H, "expires": t + ttl * 60_000,
                       "chase": bool(intent.meta.get("chase")),
                       "session": _session_of(t), "month": _dt.datetime.fromtimestamp(t / 1000.0, tz=_dt.timezone.utc).strftime("%Y-%m"),
                       "settle_hour": _dt.datetime.fromtimestamp(t / 1000.0, tz=_dt.timezone.utc).hour}
    if open_pos is not None:                 # still open at range end → mark at last close (time_stop)
        last = c15[-1]
        g_, fees, net = compute_close(open_pos["direction"], open_pos["entry_px"], float(last["c"]), open_pos["size"], "time_stop",
                                      entry_taker=bool(open_pos.get("entry_taker")))
        trades.append({**open_pos, "exit_ts": int(last["ts"]), "exit_px": float(last["c"]), "exit_reason": "end_of_range",
                       "pnl_gross": g_, "fees": fees, "pnl_net": net})
    return {"trades": trades, "stats": stats}


# ------------------------------------------------------------------------ report

def _fmt_metrics(m: dict) -> str:
    pf = m["profit_factor"]
    pf_s = "∞" if pf == float("inf") else (f"{pf:.2f}" if pf is not None else "—")
    return (f"{m['trades']} | {m['win_rate'] if m['win_rate'] is not None else '—'}% | "
            f"{m['expectancy'] if m['expectancy'] is not None else '—'} | {pf_s} | "
            f"{m['max_drawdown'] if m['max_drawdown'] is not None else '—'} | {m['longest_losing_streak']} | "
            f"{m['fee_drag_pct'] if m['fee_drag_pct'] is not None else '—'}")


def _split_table(title: str, trades: list[dict], key: str) -> list[str]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[str(t.get(key))].append(t)
    out = [f"### {title}", "", "| bucket | trades | win% | expectancy $ | PF | maxDD $ | worst streak | fee drag % |",
           "|---|---|---|---|---|---|---|---|"]
    for k in sorted(groups):
        out.append(f"| {k} | {_fmt_metrics(compute_metrics(groups[k]))} |")
    if not groups:
        out.append("| — | 0 | — | — | — | — | 0 | — |")
    out.append("")
    return out


def build_report(strategy_id: str, start_ms: int, end_ms: int, covers: dict, per_coin: dict, bars_total: int) -> str:
    all_trades = [t for r in per_coin.values() for t in r["trades"]]
    evals = sum(r["stats"]["evals"] for r in per_coin.values())
    fires = sum(r["stats"]["fires"] for r in per_coin.values())
    lines = [f"# Backtest {strategy_id}  ({_iso(start_ms)} → {_iso(end_ms)} UTC)", "",
             f"Generated {_iso(int(time.time() * 1000))} UTC · paper replay on 15m candles · equity $1,000 · 1.5% risk / 3x cap · "
             "one open position per asset · stop-before-target · fees maker 1.5 bps / taker 4.5 bps", "",
             "## Coverage (feeds over this range)", "", "| series | coverage begins | bars in range with data |", "|---|---|---|"]
    for t, c in covers.items():
        if c is None:
            frac = "none — conditions on it evaluate as unavailable (not in score)"
        elif c <= start_ms:
            frac = "100%"
        else:
            have = max(0, (end_ms - c) // _BAR)
            frac = f"{min(100.0, have / max(1, bars_total) * 100):.1f}% (from {_iso(c)})"
        lines.append(f"| `{t}` | {_iso(c)} | {frac} |")
    lines.append("")
    unav: dict[str, int] = defaultdict(int)
    warm: dict[str, int] = defaultdict(int)
    for r in per_coin.values():
        for k, v in r["stats"]["unavailable"].items():
            unav[k] += v
        for k, v in r["stats"]["warming_conds"].items():
            warm[k] += v
    met: dict[str, int] = defaultdict(int)
    evd: dict[str, int] = defaultdict(int)
    for r in per_coin.values():
        for k, v in r["stats"]["met"].items():
            met[k] += v
        for k, v in r["stats"]["evaluated"].items():
            evd[k] += v
    names = sorted(set(unav) | set(warm) | set(evd))
    if names:
        lines += ["| condition | met / evaluated | unavailable (not in score) | warming |", "|---|---|---|---|"]
        for k in names:
            lines.append(f"| {k} | {met.get(k, 0)} / {evd.get(k, 0)} | {unav.get(k, 0)} / {evals} | {warm.get(k, 0)} / {evals} |")
        lines.append("")
    lines += ["## Summary", "",
              f"- evaluations: **{evals}** · fires: **{fires}** · filled trades: **{len(all_trades)}** · "
              f"fires skipped (position already open): {sum(r['stats']['fires_skipped_open'] for r in per_coin.values())} · "
              f"entries expired unfilled: {sum(r['stats']['entries_expired'] for r in per_coin.values())} · "
              f"chase entries crossed as taker: {sum(r['stats'].get('entries_taker', 0) for r in per_coin.values())} · "
              f"evaluations with regime not allowed: {sum(r['stats']['not_allowed'] for r in per_coin.values())}", "",
              "| trades | win% | expectancy $ | PF | maxDD $ | worst streak | fee drag % |", "|---|---|---|---|---|---|---|",
              f"| {_fmt_metrics(compute_metrics(all_trades))} |", ""]
    m = compute_metrics(all_trades)
    if m["avg_mae"] is not None:
        lines.append(f"avg MAE {m['avg_mae'] * 100:.2f}% · avg MFE {m['avg_mfe'] * 100:.2f}% (of entry price)\n")
    lines += _split_table("By asset", all_trades, "asset")
    lines += _split_table("By direction", all_trades, "direction")
    lines += _split_table("By month", all_trades, "month")
    if strategy_id == "s02_funding_flow":
        lines += _split_table("By settlement hour (UTC hour of the signal)", all_trades, "settle_hour")
    else:
        lines += _split_table("By session (UTC hour of the signal)", all_trades, "session")
    lines += _split_table("By exit reason", all_trades, "exit_reason")
    if all_trades:
        lines += ["### Trades", "", "| signal (UTC) | asset | dir | entry | stop | target | exit | reason | net $ | score |", "|---|---|---|---|---|---|---|---|---|---|"]
        for t in sorted(all_trades, key=lambda x: x["signal_ts"])[-200:]:
            lines.append(f"| {_iso(t['signal_ts'])} | {t['asset']} | {t['direction']} | {t['entry_px']:.2f} | {t['stop_px']:.2f} | "
                         f"{(t['target_px'] or 0):.2f} | {t['exit_px']:.2f} | {t['exit_reason']} | {t['pnl_net']:+.2f} | {t['score']:.2f} |")
        lines.append("")
    lines += ["## Notes", "",
              "- Thresholds are the docs' values; short history evaluates over the available window (warming) exactly as live.",
              "- Sub-candle feeds (OI-1m, trades-1m, book-5s, liquidations, analytics positions/flows) only exist from their coverage start; "
              "before that the conditions built on them are 'unavailable (not in score)' — the score is the mean of the available components.",
              "- Regime in replay: macro calendar + weekend from strat_events; oracle assumed fresh at each bar close; spread unavailable (non-blocking).",
              "- s03 applies TODAY's cohort to the whole range (survivorship — labelled), with positions/flows as-of each bar.",
              "- Minute-cadence strategies evaluate once per 15m bar in replay (live: every minute)."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- api

def _paths(strategy_id: str) -> tuple[Path, Path]:
    return RESULTS_DIR / f"{strategy_id}.md", RESULTS_DIR / f"{strategy_id}.json"


def cached_report(strategy_id: str, max_age_s: int = CACHE_MAX_AGE_S) -> dict | None:
    md, meta = _paths(strategy_id)
    if not md.exists() or not meta.exists():
        return None
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if time.time() - m.get("generated_ts", 0) > max_age_s:
        return None
    return {**m, "report": md.read_text(encoding="utf-8")}


async def run_report(session, strategy_id: str, start_ms: int, end_ms: int, assets: list[str] | None = None,
                     series: dict | None = None) -> str:
    """Replays one strategy over [start, end] for every asset; writes
    backtest/results/{sid}.md + .json; returns the markdown."""
    from ..evaluator import REGISTRY
    from app.config import settings
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    strat = REGISTRY[strategy_id]
    assets = assets or settings.strategy_assets_list
    covers = {t: await coverage_start(session, t) for t in REQUIRED.get(strategy_id, ["strat_candles"])}
    cstart = covers.get("strat_candles")
    if cstart is not None and cstart > start_ms:
        start_ms = cstart                        # candles are the replay clock — start where they exist
    prow = (await session.execute(text("SELECT `key`,value FROM strat_parameters WHERE strategy_id=:s"), {"s": strategy_id})).all()
    params = {k: v for k, v in prow}
    per_coin = {}
    bars_total = 0
    t0 = time.time()
    for coin in assets:
        key = (coin, start_ms)
        if series is not None and key in series:
            d = series[key]
        else:
            d = await load_series(session, coin, start_ms, end_ms, need_cohort=True)
            if series is not None:
                series[key] = d
        if strategy_id != "s03_whale_follow":
            d = {**d, "cohort": None, "pos": [], "flows": []}     # cohort only feeds s03
        bars_total = max(bars_total, sum(1 for b in d["c15"] if b["ts"] >= start_ms))
        per_coin[coin] = await asyncio.to_thread(replay_coin, strat, coin, d, start_ms, end_ms, params)
    report = build_report(strategy_id, start_ms, end_ms, covers, per_coin, bars_total)
    md, meta = _paths(strategy_id)
    md.write_text(report, encoding="utf-8")
    meta.write_text(json.dumps({"strategy": strategy_id, "start_ms": start_ms, "end_ms": end_ms,
                                "generated_ts": int(time.time()), "seconds": round(time.time() - t0, 1),
                                "trades": sum(len(r["trades"]) for r in per_coin.values()),
                                "evals": sum(r["stats"]["evals"] for r in per_coin.values()),
                                "coverage": {k: _iso(v) for k, v in covers.items()}}), encoding="utf-8")
    logger.info("backtest %s: %d trades in %.1fs", strategy_id, sum(len(r["trades"]) for r in per_coin.values()), time.time() - t0)
    return report


async def run_all(session, days_back: int = 365) -> list[str]:
    now = int(time.time() * 1000)
    start = now - days_back * _DAY
    out = []
    series: dict = {}
    for sid in REQUIRED:
        try:
            out.append(await run_report(session, sid, start, now, series=series))
        except ReplayAborted:
            break
        except Exception:  # noqa: BLE001 — one strategy's replay fault never stops the others
            logger.exception("backtest %s failed", sid)
    return out
