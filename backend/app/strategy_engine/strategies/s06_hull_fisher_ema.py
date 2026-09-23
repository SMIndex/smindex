"""s06 Hull + Fisher + 200 EMA trend pullback (doc 06 §4/§5/§7/§15 prompt 2) —
verbatim rules over the available window. Two instances: gated
(s06_hull_fisher_ema) and ungated (s06u_hull_fisher_ema). EMA200 is seeded from
the first available close and labelled "warming: M of 200 bars" until 200
closed 15m candles exist — it is never "not enough history".
"""
from __future__ import annotations

import numpy as np

from ..features import indicators as ind
from ..intents import OrderIntent
from . import common as cm
from .base import Cond, EvalResult, PaperStrategy, StrategyContext, UNAVAILABLE, avail, bias_align, ohlc, regime_cond, regime_blockers

_EMA = 200
_HULL = 55


def band_occupancy(ohlc: list[dict], lo: float, hi: float, min_bars: int = 400) -> tuple[float, int] | None:
    """(share of bars with lo <= |close - EMA200| / ATR14 <= hi, bars counted) over
    the given 15m window, skipping the first `min_bars` so EMA200/ATR are seeded.
    None when the window is too short. Pure; unit-tested."""
    if len(ohlc) <= min_bars + 1:
        return None
    h = [float(x["h"]) for x in ohlc]; l = [float(x["l"]) for x in ohlc]; c = [float(x["c"]) for x in ohlc]
    e = ind.ema(c, _EMA)
    a = ind.atr_warm(h, l, c, 14)
    cc = np.asarray(c, dtype=float)
    ok = np.isfinite(e) & np.isfinite(a) & (a > 0)
    ok[:min_bars] = False
    n = int(ok.sum())
    if n == 0:
        return None
    d = np.abs(cc[ok] - e[ok]) / a[ok]
    return float(((d >= lo) & (d <= hi)).mean()), n


class HullFisherEma(PaperStrategy):
    cadence = "candle_15m"

    def __init__(self, gated: bool = True) -> None:
        self.gated = gated
        self.id = "s06_hull_fisher_ema" if gated else "s06u_hull_fisher_ema"

    def evaluate(self, ctx: StrategyContext) -> EvalResult:  # noqa: C901
        thr_key = "fire_threshold_gated" if self.gated else "fire_threshold_ungated"
        r = EvalResult(coin=ctx.coin, fire_threshold=ctx.p(thr_key, 0.7 if self.gated else 0.5))
        tag = "gated" if self.gated else "ungated"
        r.labels["variant"] = tag
        c = ctx.candles_15m
        have = len(c)
        conds: list[Cond] = []
        highs, lows, closes = ohlc(c)
        ema_warm = avail(have, _EMA, "bars")
        hull_warm = avail(have, cm.hull_need(_HULL), "bars")
        atr, atr_warm = cm.atr_cond_value(ctx, 14)

        ema = ind.ema(closes, _EMA) if have else np.array([])
        hma = ind.hull_ma(closes, _HULL) if have else np.array([])
        hsl = ind.slope(hma) if hma.size else np.array([])
        fisher, signal = ind.fisher_transform(highs, lows, 9) if have >= 3 else (np.array([]), np.array([]))

        def hdir_at(i: int) -> int:
            if hsl.size < 2 or i < 1 or i >= hsl.size or not (np.isfinite(hsl[i]) and np.isfinite(hsl[i - 1])):
                return 0
            if hsl[i] > 0 and hsl[i - 1] > 0:
                return 1
            if hsl[i] < 0 and hsl[i - 1] < 0:
                return -1
            return 0

        # Direction
        direction = None
        hd = hdir_at(have - 1)
        e_last = float(ema[-1]) if ema.size and np.isfinite(ema[-1]) else float("nan")
        if have and np.isfinite(e_last):
            if closes[-1] > e_last and hd == 1:
                direction = "long"
            elif closes[-1] < e_last and hd == -1:
                direction = "short"
        conds.append(Cond("Direction", "close vs EMA200 with Hull(55) slope (2 bars)",
                          value=(f"{direction or 'no trend'} (close {'above' if have and np.isfinite(e_last) and closes[-1] > e_last else 'below'} EMA, hull55={hd:+d})" if have else "no candles"),
                          met=(direction is not None) if have else None, warming=(ema_warm or hull_warm)))
        d = (1 if direction == "long" else -1) if direction else 0

        # Trend established: >= 12 consecutive closes on the trend side of EMA200
        run = 0
        if have and np.isfinite(e_last):
            side = 1 if closes[-1] > e_last else -1
            for i in range(have - 1, -1, -1):
                if not np.isfinite(ema[i]):
                    break
                if (1 if closes[i] > ema[i] else -1) != side:
                    break
                run += 1
        trend_ok = (run >= ctx.p("trend_min_candles", 12)) if (direction and have) else (None if not have else False)
        conds.append(Cond("Trend established", ">= 12 consecutive closes on the trend side of EMA200",
                          value=f"{run} consecutive closes", threshold="need 12", met=trend_ok, warming=ema_warm))

        # Not overextended: 0.5..3.0 ATR from EMA200
        dist_atr = abs(closes[-1] - e_last) / atr if (have and np.isfinite(e_last) and np.isfinite(atr) and atr > 0) else None
        ext_ok = (ctx.p("ext_min_atr", 0.5) <= dist_atr <= ctx.p("ext_max_atr", 3.0)) if dist_atr is not None else None
        conds.append(Cond("Not overextended", "distance from EMA200 between 0.5 and 3.0 ATR",
                          value=(f"{dist_atr:.2f} ATR" if dist_atr is not None else "n/a"), threshold="0.5–3.0",
                          met=ext_ok, warming=(ema_warm or atr_warm)))
        # Band occupancy over the 31d window — makes the rule's rarity visible on the
        # detail page (owner 2026-09-04: band stays verbatim, revisit after a week of paper).
        band = band_occupancy(ctx.ohlc_15m_30d, ctx.p("ext_min_atr", 0.5), ctx.p("ext_max_atr", 3.0))
        if band is not None:
            r.labels["ema_band"] = f"inside 0.5–3 ATR of EMA200 on {band[0]:.0%} of the last {band[1]} 15m bars"

        # Pullback: 2-8 candles moving toward Hull55; extreme within 0.5 ATR of Hull55 or between Hull55 and EMA; no close beyond EMA
        pb_len = 0; pb_extreme = None; pb_ok = None; pb_val = "no trend"
        if direction and have >= 3 and np.isfinite(atr) and atr > 0:
            # count candles (ending at the last closed one) whose extreme moved toward Hull55
            i = have - 1
            while i >= 1 and pb_len < 8:
                if not (np.isfinite(hma[i]) and np.isfinite(hma[i - 1])):
                    break
                if direction == "long":
                    toward = lows[i] <= lows[i - 1] or lows[i] - hma[i] <= 0.5 * atr
                else:
                    toward = highs[i] >= highs[i - 1] or hma[i] - highs[i] <= 0.5 * atr
                if not toward:
                    break
                pb_len += 1
                i -= 1
            seg = range(have - pb_len, have)
            if pb_len:
                pb_extreme = min(lows[j] for j in seg) if direction == "long" else max(highs[j] for j in seg)
                h_now = float(hma[-1]) if np.isfinite(hma[-1]) else None
                if h_now is not None:
                    if direction == "long":
                        touch = (pb_extreme - h_now <= 0.5 * atr) or (e_last <= pb_extreme <= h_now)
                        beyond = any(closes[j] < ema[j] for j in seg if np.isfinite(ema[j]))
                    else:
                        touch = (h_now - pb_extreme <= 0.5 * atr) or (h_now <= pb_extreme <= e_last)
                        beyond = any(closes[j] > ema[j] for j in seg if np.isfinite(ema[j]))
                    pb_ok = (2 <= pb_len <= 8) and touch and not beyond
                    pb_val = f"{pb_len} candle(s), extreme {cm.fmt_px(pb_extreme)} ({(pb_extreme - h_now) / atr:+.2f} ATR vs Hull55)" + (" · closed beyond EMA" if beyond else "")
            else:
                pb_val = "no pullback candles"
                pb_ok = False
        conds.append(Cond("Pullback", "2-8 candles toward Hull(55), within 0.5 ATR, no close beyond EMA",
                          value=pb_val, met=pb_ok, warming=(hull_warm or atr_warm)))

        # Fisher trigger: reached <= -1.5 (long) during the pullback and crosses above signal on the closed candle
        f_ok = None; f_val = "n/a"; f_ext = None
        fw = avail(have, 9 + 6, "bars")
        if direction and fisher.size >= 2 and np.isfinite(fisher[-1]) and np.isfinite(signal[-1]) and np.isfinite(fisher[-2]) and np.isfinite(signal[-2]):
            look = max(pb_len, 1)
            recent = fisher[-look - 1:]
            recent = recent[np.isfinite(recent)]
            if direction == "long":
                f_ext = float(recent.min()) if recent.size else None
                cross = fisher[-1] > signal[-1] and fisher[-2] <= signal[-2]
                f_ok = bool(recent.size and recent.min() <= -1.5 and cross)
            else:
                f_ext = float(recent.max()) if recent.size else None
                cross = fisher[-1] < signal[-1] and fisher[-2] >= signal[-2]
                f_ok = bool(recent.size and recent.max() >= 1.5 and cross)
            f_val = f"fisher {fisher[-1]:+.2f} vs signal {signal[-1]:+.2f}, extreme {f_ext:+.2f}" if f_ext is not None else f"fisher {fisher[-1]:+.2f}"
        conds.append(Cond("Fisher trigger", "Fisher(9) reached <= -1.5 then crosses signal", value=f_val, met=f_ok, warming=fw))

        conds.append(Cond("Hull still up", "Hull(55) slope still with trend on the trigger",
                          value=f"hull55={hd:+d}", met=((hd == d) if direction else None), warming=hull_warm))
        hd1, w1 = cm.hull_dir_warm([float(x["c"]) for x in ctx.candles_1h], 21)
        conds.append(Cond("1h Hull", "1h Hull(21) slope agrees", value=f"hull1h={hd1:+d}",
                          met=((hd1 == d) if direction else None), warming=w1))
        bias_score = (ctx.bias or {}).get("bias_score")
        b_ok = bias_align(bias_score, direction)
        conds.append(Cond("Bias not against", "bias x direction >= -0.2",
                          value=(f"{float(bias_score):+.2f}" if bias_score is not None else "no bias row"),
                          met=b_ok, warming=(None if ctx.bias else UNAVAILABLE)))

        gate_ok = None; gate_fail: list[str] = []
        if self.gated:
            frac, _hm, oi_warm = cm.oi_change(ctx.oi_1m, 4 * 3_600_000, ctx.now_ms)
            pchg = cm.price_change(ctx.oi_1m, 4 * 3_600_000, ctx.now_ms)
            if pchg is None and len(closes) >= 17:
                pchg = closes[-1] / closes[-17] - 1.0
            oi_ok = None
            if frac is not None and pchg is not None:
                oi_ok = (np.sign(frac) == np.sign(pchg)) and abs(frac) >= ctx.p("oi_4h_min", 0.01)
            conds.append(Cond("OI with price", "OI 4h change same sign as price, |OI| >= 1%",
                              value=(f"OI {frac:+.2%} / px {pchg:+.2%}" if frac is not None and pchg is not None else "no OI tape"),
                              threshold="need |OI| >= 1%", met=oi_ok, warming=oi_warm))
            cc, crowd_ok = cm.crowd_cond(ctx, direction)
            cc.name = "Funding not extreme"; cc.subline = "gauge crowding_level != EXTREME on the trade side"
            conds.append(cc)
            ratio, wv = cm.rv_1h_vs_24h(closes)
            rv_ok = (ratio > 1.0) if ratio is not None else None
            conds.append(Cond("Realized vol", "last 1h realized vol > its 24h average",
                              value=(f"{ratio:.2f}x 24h avg" if ratio is not None else "n/a"), threshold="need > 1.0x",
                              met=rv_ok, warming=wv))
            blockers = regime_blockers(ctx.regime)
            macro_ok = ("macro_event" not in blockers) if ctx.regime else None
            conds.append(Cond("No macro", "no macro event within next 2h / last 30m",
                              value=("macro window" if macro_ok is False else ("clear" if macro_ok else "no regime row")),
                              met=macro_ok, warming=(None if ctx.regime else UNAVAILABLE)))
            rc = regime_cond(ctx)
            conds.append(rc)
            gate_conds = [(oi_ok, "OI with price"), (crowd_ok, "Funding not extreme"), (rv_ok, "Realized vol"),
                          (macro_ok, "No macro"), (rc.met, "Regime gate")]
            gate_fail = [n for ok, n in gate_conds if ok is False]
            evaluated = [ok for ok, _ in gate_conds if ok is not None]
            gate_ok = (not gate_fail) if evaluated else None

        r.conditions = conds
        r.direction = direction
        trend_q = None
        if direction and trend_ok is not None:
            hmag = abs(float(hsl[-1])) / atr if (hsl.size and np.isfinite(hsl[-1]) and np.isfinite(atr) and atr > 0) else 0.0
            trend_q = ctx.p("score_trend", 0.25) * (0.7 * min(1.0, run / 12.0) + 0.3 * min(1.0, hmag / 0.2)) if trend_ok else 0.0
        pb_q = None
        if pb_ok is not None:
            pb_q = ctx.p("score_pullback", 0.2) * (1.0 if (pb_ok and pb_extreme is not None) else 0.0)
        r.components = {
            "trend": trend_q if direction else (0.0 if have else None),
            "pullback": pb_q,
            "fisher": (ctx.p("score_fisher", 0.2) if f_ok else (None if f_ok is None else 0.0)),
            "gate": ((ctx.p("score_gate", 0.25) if gate_ok else (None if gate_ok is None else 0.0)) if self.gated else 0.0),
            "bias": (ctx.p("score_bias", 0.1) if b_ok else (None if b_ok is None else 0.0)),
        }
        r.score_from_components()
        r.collect_warming()

        failing = [x.name for x in conds if x.met is False]
        if not direction:
            r.waiting_for = f"No qualifying trend — close {'above' if have and np.isfinite(e_last) and closes[-1] > e_last else 'below'} EMA200, hull55={hd:+d} [{tag}]" if have else f"No candles [{tag}]"
        elif not trend_ok:
            r.waiting_for = f"Trend {direction} forming — {run}/12 closes beyond EMA200 [{tag}]"
        elif not pb_ok:
            r.waiting_for = f"Trend {direction} ({run} closes) — watching for a 2–8 candle pullback to Hull55 [{tag}]"
            if run == 12:
                r.alerts.append(("pre", f"[S06 TREND] {ctx.coin} {direction} trend established: {run} closes beyond EMA200, Hull55 {hd:+d} [{tag}]"))
        elif not f_ok:
            r.waiting_for = f"Pullback {pb_len} candles in {direction} trend — waiting for Fisher turn ({f_val}) [{tag}]"
        else:
            r.waiting_for = (f"Fisher turned in {direction} trend" + (f" — failing: {', '.join(failing)}" if failing else
                             (f" — score {r.total_score:.2f} < {r.fire_threshold:.2f}" if r.total_score is not None and r.total_score < r.fire_threshold else "")) + f" [{tag}]")
            if self.gated and gate_fail:
                r.alerts.append(("skip", f"[S06 TREND] {ctx.coin} gate CLOSED: {', '.join(gate_fail)}"))

        stop_needed = None
        fired = (direction is not None and bool(trend_ok) and bool(pb_ok) and bool(f_ok) and r.total_score is not None
                 and r.total_score >= r.fire_threshold and all(x.met is not False for x in conds)
                 and (bool(gate_ok) if self.gated else True))
        if fired and pb_extreme is not None and np.isfinite(atr) and atr > 0:
            off = ctx.p("entry_offset_atr", 0.1) * atr
            entry_px = closes[-1] - off if direction == "long" else closes[-1] + off
            stop = pb_extreme - ctx.p("stop_beyond_atr", 0.2) * atr if direction == "long" else pb_extreme + ctx.p("stop_beyond_atr", 0.2) * atr
            stop_needed = abs(entry_px - stop) / atr
            if stop_needed > ctx.p("stop_max_atr", 1.2):
                fired = False
                r.waiting_for = f"Setup {direction} but stop needs {stop_needed:.2f} ATR (> 1.2) — skipped [{tag}]"
                r.alerts.append(("skip", f"[S06 PULLBACK] {ctx.coin} {direction} skipped: stop {stop_needed:.2f} ATR > 1.2 [{tag}]"))
            else:
                rr = abs(entry_px - stop)
                t1 = entry_px + ctx.p("target_r", 1.5) * rr if direction == "long" else entry_px - ctx.p("target_r", 1.5) * rr
                r.intents = [OrderIntent(self.id, "hl", ctx.coin, direction, "entry", "post_only_limit", 0.0, px=entry_px,
                                         reason=f"S06 pullback [{tag}]",
                                         meta={"stop": stop, "target1": t1, "atr": atr, "time_stop_h": int(ctx.p("time_stop_hours", 6)),
                                               "entry_valid_min": int(ctx.p("entry_valid_candles", 2) * 15),
                                               "size_mult": ctx.p("size_mult_first30", 0.5), "variant": tag})]
                r.waiting_for = f"Pullback {direction} FIRED — entry {cm.fmt_px(entry_px)} stop {cm.fmt_px(stop)} t1 {cm.fmt_px(t1)} [{tag}]"
                r.alerts.append(("fire", f"[S06 PULLBACK] {ctx.coin} {direction.upper()} | Fisher turn from {f_ext:+.2f} | entry {entry_px:.1f} stop {stop:.1f} t1 {t1:.1f} | score {r.total_score:.2f} [{tag}] (paper)"))
        r.fired = bool(fired and r.intents)
        return r
