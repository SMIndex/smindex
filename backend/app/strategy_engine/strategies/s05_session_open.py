"""s05 Session open momentum (doc 05 §4/§6/§13 prompt 2) — verbatim rules,
evaluated on whatever data exists (warming labels, no history short-circuits)."""
from __future__ import annotations

import datetime as _dt

import numpy as np

from ..features import sessions as sess
from ..intents import OrderIntent
from . import common as cm
from .base import Cond, EvalResult, PaperStrategy, StrategyContext, UNAVAILABLE, avail, bias_align, ohlc, regime_cond

_UTC = _dt.timezone.utc


def _next_session_text(now_ms: int) -> str:
    """'next EU 07:00 UTC (in 2h 10m)' from the session windows."""
    u = _dt.datetime.fromtimestamp(now_ms / 1000.0, tz=_UTC)
    best = None
    for day_off in (0, 1, 2, 3):
        d = u + _dt.timedelta(days=day_off)
        for name, w in sess.session_windows(d).items():
            start = d.replace(hour=0, minute=0, second=0, microsecond=0) + _dt.timedelta(minutes=w["or_start"])
            if start > u and (best is None or start < best[1]):
                best = (name, start)
        if best:
            break
    if not best:
        return "next session: none in 3 days"
    name, start = best
    mins = int((start - u).total_seconds() // 60)
    return f"next {name} {start:%H:%M} UTC (in {mins // 60}h {mins % 60:02d}m)"


class SessionOpenMomentum(PaperStrategy):
    """chase=False → doc 05 verbatim (entry = OR edge ± 0.1 ATR, a pullback order).
    chase=True → s05c, the owner-approved comparison row (2026-09-04): IDENTICAL
    rules, only the entry differs — post-only limit at the BREAK CANDLE CLOSE
    ± 0.1 ATR, re-quoted per the paper executor's rules and taken (taker fee) if
    it must cross. Stop/target/time-stop formulas unchanged (relative to the entry)."""
    id = "s05_session_open"
    cadence = "candle_15m"

    def __init__(self, chase: bool = False) -> None:
        self.chase = chase
        if chase:
            self.id = "s05c_session_open"

    def evaluate(self, ctx: StrategyContext) -> EvalResult:
        r = EvalResult(coin=ctx.coin, fire_threshold=ctx.p("fire_threshold", 0.65))
        name, phase = sess.current_session(ctx.now_ms)
        c = ctx.candles_15m
        highs, lows, closes = ohlc(c)
        atr, atr_warm = cm.atr_cond_value(ctx, 14)
        conds: list[Cond] = []

        # opening range for the active session — TODAY's stored 15m candles only
        orng = None
        if name:
            win = sess.session_windows(ctx.now_ms).get(name, {})
            today = [x for x in c if int(x["ts"]) >= cm.utc_day_start(ctx.now_ms)]
            orng = sess.opening_range_from_candles(today, win.get("or_start", 0), win.get("or_end", 0))
        or_h = (orng["or_high"] - orng["or_low"]) if orng else None

        # 1) high-vol day: RV24h pct(30d) >= 60 OR OR height >= 1.2 ATR
        rv, rv_pct, rv_days = cm.rv24h_pct(ctx.closes_15m_30d or closes, 30)
        rv_ok = (rv_pct >= ctx.p("rv_pct_min", 60.0)) if np.isfinite(rv_pct) else None
        height_ok = (or_h is not None and np.isfinite(atr) and atr > 0 and or_h >= ctx.p("or_height_atr", 1.2) * atr)
        volday = bool(rv_ok) or bool(height_ok)
        rv_txt = f"RV24h {rv_pct:.0f}th pct" if np.isfinite(rv_pct) else "RV24h n/a"
        or_txt = (f"OR {or_h / atr:.2f} ATR" if (or_h is not None and np.isfinite(atr) and atr > 0)
                  else ("no OR yet" if name else "no session"))
        conds.append(Cond("High-vol day", "RV24h >= 60th pct(30d) OR OR height >= 1.2 ATR",
                          value=f"{rv_txt} · {or_txt}", threshold="need 60th pct or 1.2 ATR",
                          met=(volday if (rv_ok is not None or or_h is not None) else None),
                          warming=(avail(rv_days, 30, "days of RV") if rv_ok is not None or rv_days else
                                   (avail(len(closes), 97, "bars")))))

        # 2) direction from the break
        last = c[-1] if c else None
        direction = None
        broke = False
        if orng and last and np.isfinite(atr) and atr > 0:
            if float(last["c"]) >= orng["or_high"] + ctx.p("break_atr", 0.1) * atr:
                direction, broke = "long", True
            elif float(last["c"]) <= orng["or_low"] - ctx.p("break_atr", 0.1) * atr:
                direction, broke = "short", True
        conds.append(Cond("OR break", "15m close beyond OR by >= 0.1 ATR",
                          value=("broke " + direction if broke else ("inside range" if orng else
                                 ("opening range forming" if phase == "opening_range" else
                                  ("no OR candles today" if name else "no session")))),
                          threshold=(f"OR {cm.fmt_px(orng['or_low'])}–{cm.fmt_px(orng['or_high'])}" if orng else None),
                          met=(broke if orng else None), warming=atr_warm))

        # 3) bias alignment
        bias_score = (ctx.bias or {}).get("bias_score")
        bias_ok = bias_align(bias_score, direction)
        conds.append(Cond("Bias alignment", "bias x direction >= -0.2",
                          value=(f"{float(bias_score):+.2f}" if bias_score is not None else "no bias row"),
                          met=bias_ok, warming=(None if ctx.bias else UNAVAILABLE)))

        # 4) timing: 1h hull agrees OR flat with 15m fisher turning
        hd, w1 = cm.hull_dir_warm([float(x["c"]) for x in ctx.candles_1h], 21)
        ft, w2 = cm.fisher_turn_warm(highs, lows, 9)
        timing_ok = None
        if direction:
            d = 1 if direction == "long" else -1
            timing_ok = (hd == d) or (hd == 0 and ft == d)
        conds.append(Cond("Trend/timing", "1h Hull agrees, or flat with 15m Fisher turning",
                          value=f"hull1h={hd:+d} fisher15m={ft:+d}", met=timing_ok, warming=(w1 or w2)))

        # 5) funding gauge not EXTREME on the trade side
        cc, gauge_ok = cm.crowd_cond(ctx, direction)
        conds.append(cc)

        # 6) regime gate
        rc = regime_cond(ctx)
        conds.append(rc)
        reg_ok = rc.met

        # 7) volume: break candle taker vol >= 1.3x 20-candle avg
        vc, vol_ok = cm.volume_cond(ctx, ctx.p("taker_vol_mult", 1.3), 20)
        if not broke:
            vc.met = None
            vc.value = (vc.value + " (no break candle)") if vc.value and not vc.warming else vc.value
            vol_ok = None
        conds.append(vc)

        r.conditions = conds
        r.direction = direction
        r.components = {
            "trigger": (ctx.p("score_volday", 0.25) if volday else 0.0) + (ctx.p("score_break", 0.2) if broke else 0.0),
            "bias": ctx.p("score_bias", 0.2) if bias_ok else (None if bias_ok is None else 0.0),
            "timing": ctx.p("score_timing", 0.2) if timing_ok else (None if timing_ok is None else 0.0),
            "volume": ctx.p("score_volume", 0.15) if vol_ok else (None if vol_ok is None else 0.0),
            "regime": None,
        }
        r.score_from_components()
        r.collect_warming()

        # waiting-for sentence (design spec §5)
        if not name:
            r.waiting_for = f"No session open — {_next_session_text(ctx.now_ms)}"
        elif phase == "opening_range":
            r.waiting_for = f"{name} opening range forming ({rv_txt})"
        elif not orng:
            r.waiting_for = f"{name} open — no opening-range candles stored today"
        elif not volday:
            r.waiting_for = f"{name} session, low-vol day so far ({rv_txt}, {or_txt}) — watching"
            r.alerts.append(("skip", f"[S05 SESSION] {name} open, low-vol day — skip"))
        elif not broke:
            r.waiting_for = f"{name} vol-day YES, waiting for OR break ({cm.fmt_px(orng['or_low'])}–{cm.fmt_px(orng['or_high'])})"
            r.alerts.append(("pre", f"[S05 SESSION] {name} open, vol-day YES, OR {orng['or_low']:.1f} to {orng['or_high']:.1f}, watching"))
        else:
            failing = [x.name for x in conds if x.met is False]
            r.waiting_for = (f"{name} break {direction}, checking confirmations"
                             + (f" — failing: {', '.join(failing)}" if failing else
                                (f" — score {r.total_score:.2f} < {r.fire_threshold:.2f}" if r.total_score is not None and r.total_score < r.fire_threshold else "")))

        allc = [c_.met for c_ in conds]
        fired = (r.total_score is not None and r.total_score >= r.fire_threshold
                 and broke and bool(reg_ok) and all(m is not False for m in allc))
        r.fired = bool(fired)
        if fired and direction and np.isfinite(atr) and atr > 0 and orng:
            # s05: OR edge ± 0.1 ATR (pullback).  s05c: break candle close ± 0.1 ATR (chase).
            edge = (float(last["c"]) if self.chase else
                    (orng["or_high"] if direction == "long" else orng["or_low"]))
            off = ctx.p("entry_offset_atr", 0.1) * atr
            entry_px = edge + off if direction == "long" else edge - off
            mid = (orng["or_high"] + orng["or_low"]) / 2
            stop = mid if abs(entry_px - mid) <= ctx.p("stop_atr", 0.8) * atr else (
                entry_px - ctx.p("stop_atr", 0.8) * atr if direction == "long" else entry_px + ctx.p("stop_atr", 0.8) * atr)
            t1 = entry_px + or_h if direction == "long" else entry_px - or_h
            tag = "S05C" if self.chase else "S05"
            r.intents = [
                OrderIntent(self.id, "hl", ctx.coin, direction, "entry", "post_only_limit", 0.0, px=entry_px,
                            reason=f"{tag} OR break" + (" chase" if self.chase else ""),
                            meta={"stop": stop, "target1": t1, "atr": atr, "time_stop_h": 3,
                                  "entry_valid_min": int(ctx.p("entry_valid_candles", 2) * 15),
                                  "chase": self.chase}),
            ]
            r.waiting_for = f"{name} break {direction} FIRED — entry {cm.fmt_px(entry_px)} stop {cm.fmt_px(stop)} t1 {cm.fmt_px(t1)}"
            r.alerts.append(("fire", f"[{tag} SESSION] {ctx.coin} {direction.upper()} {name}-open break | entry {entry_px:.1f} stop {stop:.1f} t1 {t1:.1f} | score {r.total_score:.2f} (paper)"))
        return r
